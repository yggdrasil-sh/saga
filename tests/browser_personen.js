// Browser-Test Personen- und Datei-Eigenschaft (2.5 Paket 1 Etappen 2/3) gegen einen laufenden Test-Server:
//   SAGA_BASE=http://127.0.0.1:8210 SAGA_USER=konto SAGA_PASS=... SAGA_OTHER=<handle einer Person mit Konto> NODE_PATH=$(npm root -g) \
//   PLAYWRIGHT=playwright PW_CHANNEL=chromium node tests/browser_personen.js
// Prueft: Spalten "Person"/"Dateien" anlegen, Auswahl zeigt nur Personen mit Zugriff (/people), Chips mit Namen, eigener
// Name; Filter "ist mir zugewiesen" (Web = Server), Board nach Person (nur lesend), Datei hochladen ueber das Menue,
// Datei-Chip mit Link, entfernen; kein Anmeldename der anderen Person im DOM.
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core'), ENGINE = process.env.PW_BROWSER || 'chromium';
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
const OTHER = process.env.SAGA_OTHER, OTHER_LOGIN = process.env.SAGA_OTHER_LOGIN, OTHER_NAME = process.env.SAGA_OTHER_NAME;
if (!BASE || !USER || !PASS || !OTHER) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS, SAGA_OTHER setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 600))); }

async function session(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 860 }, locale: 'de-DE', timezoneId: 'Europe/Berlin' });
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
  p.open = async (id) => { await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector('.db-row, .dbl-row, .board-card, .db-new-row'); await p.waitForTimeout(700); };
  return p;
}
async function until(p, fn, arg, ms = 15000) {      // wie waitForFunction, aber ohne eval (Saga erlaubt kein 'unsafe-eval')
  for (const t0 = Date.now(); Date.now() - t0 < ms; await p.waitForTimeout(100)) if (await p.evaluate(fn, arg)) return true;
  return false;
}
function view(p, D, vid) { return p.evaluate(([D, vid]) => { localStorage.setItem('hk-db-view-' + D, vid); }, [D, vid]); }

(async () => {
  const browser = await PW[ENGINE].launch({ headless: true, channel: ENGINE === 'chromium' ? (process.env.PW_CHANNEL || 'chrome') : undefined });
  let D = null, p = null;
  try {
    p = await session(browser);
    D = (await p.api('POST', '/api/notes', { kind: 'database', title: 'Personen-Test' })).id;
    const s = (await p.api('GET', '/api/notes/' + D)).schema;
    s.props = [{ id: 'st', name: 'Status', type: 'select', options: [{ id: 'a', name: 'A', color: 'gray' }] }];
    s.views = [{ id: 'tab', name: 'Tabelle', type: 'table' }, { id: 'mir', name: 'Mir', type: 'table', filters: [{ pid: 'wer', op: 'me', value: '' }] },
               { id: 'brd', name: 'Board', type: 'board', group_by: 'wer' }];
    await p.api('PATCH', '/api/notes/' + D, { schema: s });
    for (let i = 0; i < 3; i++) await p.api('POST', '/api/notes', { parent_id: D, title: 'Zeile ' + i });
    await view(p, D, 'tab'); await p.open(D);

    // --- Spalte "Person" anlegen ---
    await p.click('button.db-add-col');
    ok('Menue „Neue Spalte“ offen', await until(p, () => !!document.querySelector('.menu:not([hidden]) .menu-item')));
    const pitem = await p.$('.menu:not([hidden]) .menu-item:has-text("Person")');
    ok('Spaltenmenue bietet „Person“ an', !!pitem);
    await pitem.click();
    ok('Spalte Person angelegt', await until(p, () => [...document.querySelectorAll('th')].some((x) => /Person/.test(x.textContent))));
    let sch = (await p.api('GET', '/api/notes/' + D + '/rows?limit=1')).schema;
    const pid = (sch.props.find((x) => x.type === 'person') || {}).id;
    // Spalten-ID 'wer' fuer die Ansichten (Filter/Board): umbenennen ueber das Schema
    sch.props.find((x) => x.id === pid).id = 'wer';
    sch.views = s.views;                                 // Ansichten erst jetzt (Filter/Board auf 'wer' braucht die Spalte)
    await p.api('PATCH', '/api/notes/' + D, { schema: sch });
    await p.open(D);
    // Auswahl oeffnen (erste Zeile)
    const cell = await p.$('tr.db-row td.db-cell .val-person button.val-btn');
    ok('Personen-Zelle mit Knopf', !!cell);
    await cell.click();
    ok('Auswahl zeigt Personen (nur ich, keine Freigabe)', await until(p, () => document.querySelectorAll('.menu:not([hidden]) .menu-opts .menu-item').length === 1));
    await p.click('.menu:not([hidden]) .menu-opts .menu-item');
    ok('eigener Name als Chip', await until(p, () => !!document.querySelector('tr.db-row .pp-chip')));
    // Freigabe an die andere Person -> sie erscheint in der Auswahl
    await p.api('PUT', '/api/notes/' + D + '/shares', { type: 'user', target: OTHER, role: 'view' });
    const cells = await p.$$('tr.db-row td.db-cell .val-person button.val-btn');
    await cells[1].click();
    ok('Auswahl nach Freigabe: zwei Personen', await until(p, () => document.querySelectorAll('.menu:not([hidden]) .menu-opts .menu-item').length === 2));
    if (OTHER_NAME) {
      await p.fill('.menu:not([hidden]) .menu-input', OTHER_NAME.split(' ').pop());
      ok('Suche nach Anzeigename', await until(p, () => document.querySelectorAll('.menu:not([hidden]) .menu-opts .menu-item').length === 1));
    }
    const other = await p.$('.menu:not([hidden]) .menu-opts .menu-item:not(:has(.mi-hint))');
    await other.click();
    ok('andere Person als Chip (Anzeigename)', await until(p, (n) => [...document.querySelectorAll('tr.db-row .pp-chip')].some((x) => !n || x.textContent.includes(n)), OTHER_NAME || ''));
    // Filter "ist mir zugewiesen": Web = Server
    await view(p, D, 'mir'); await p.open(D);
    const web = await p.evaluate(() => [...document.querySelectorAll('tr.db-row')].map((x) => +x.dataset.id));
    const srv = (await p.api('GET', '/api/notes/' + D + '/rows?view=mir')).rows.map((x) => x.id);
    ok('Filter „mir“: Web = Server (eine Zeile)', web.length === 1 && JSON.stringify(web) === JSON.stringify(srv), { web, srv });
    // Board nach Person: Spalten = Kombinationen, nur lesend
    await view(p, D, 'brd'); await p.open(D);
    const cols = await p.evaluate(() => [...document.querySelectorAll('.board-col')].map((c) => c.querySelectorAll('.board-card').length));
    ok('Board nach Person: ohne + zwei Gruppen', cols.length === 3 && cols.reduce((a, b) => a + b, 0) === 3, cols);
    ok('Board nach Person: Spalten mit Personen-Chips', await p.evaluate(() => document.querySelectorAll('.board-head .pp-chip').length >= 2));

    // --- Spalte "Dateien": hochladen, Chip, entfernen ---
    await view(p, D, 'tab'); await p.open(D);
    await p.click('button.db-add-col');
    await until(p, () => !!document.querySelector('.menu:not([hidden]) .menu-item'));
    await (await p.$('.menu:not([hidden]) .menu-item:has-text("Dateien")')).click();
    ok('Spalte Dateien angelegt', await until(p, () => !!document.querySelector('tr.db-row .val-files button.val-btn')));
    await p.click('tr.db-row .val-files button.val-btn');
    ok('Datei-Menue mit „hochladen“', await until(p, () => !!document.querySelector('.menu:not([hidden]) input[type=file]')));
    await p.setInputFiles('.menu:not([hidden]) input[type=file]', { name: 'notiz.txt', mimeType: 'text/plain', buffer: Buffer.from('Hallo Saga') });
    ok('Datei-Chip erscheint', await until(p, () => [...document.querySelectorAll('tr.db-row .fl-chip')].some((x) => x.textContent.includes('notiz.txt'))));
    const rows = (await p.api('GET', '/api/notes/' + D + '/rows')).rows;
    const f = rows.map((r) => Object.values(r.props).find((v) => Array.isArray(v) && v[0] && v[0].url)).find(Boolean);
    ok('Server: Datei mit Groesse', !!f && f[0].name === 'notiz.txt' && f[0].size === 10, f);
    await p.click('tr.db-row .val-files button.val-btn');
    ok('Menue zeigt Datei-Link', await until(p, () => !!document.querySelector('.menu:not([hidden]) a.fl-chip[href^="/files/"]')));
    await p.click('.menu:not([hidden]) .fl-line .flt-x');
    ok('Datei entfernt', await until(p, () => !document.querySelector('tr.db-row .fl-chip')));
    // Datenschutz: kein Anmeldename der anderen Person im DOM
    if (OTHER_LOGIN) ok('kein Anmeldename der anderen Person in der Seite', !(await p.content()).includes(OTHER_LOGIN));
  } catch (e) {
    console.log('ABBRUCH', e && e.stack || e); res.push(false);
  } finally {
    if (D && p) await p.api('DELETE', '/api/notes/' + D).catch(() => {});
    await browser.close();
  }
  console.log(`\nBROWSER-PERSONEN ${res.filter(Boolean).length}/${res.length} - ` + (res.length && res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(res.length && res.every(Boolean) ? 0 : 1);
})();
