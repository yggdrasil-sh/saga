// Browser-Test Zeitleiste/Gantt (2.5, Paket 1 E4) und Konto-Einstellungen "API-Tokens" (E5) gegen einen laufenden Test-Server:
//   SAGA_BASE=http://127.0.0.1:8280 SAGA_USER=konto SAGA_PASS=... [SAGA_USER2/SAGA_PASS2 = Konto mit Leserecht]
//   NODE_PATH=$(npm root -g) PLAYWRIGHT=playwright PW_CHANNEL=chromium node tests/browser_zeitleiste.js
// Prueft: Zeitleiste mit Start/Ende aus zwei Datums-Eigenschaften und mit Zeitraum im Datum, Skalen Tag/Woche/Monat/Quartal
// (Umschalter, gespeichert), Gruppen, "ohne Termin", Balken ziehen = verschieben (beide Daten), rechten Griff ziehen =
// verlaengern, Tastatur (Alt+Pfeil), Abhaengigkeiten als Pfeile, Nur-Lesen ohne Griffe/Ziehen, Handy-Breite ohne Seiten-Scroll;
// Token-UI: Anlegen mit Bestaetigung (Passwort), Klartext einmal, Liste ohne Klartext, Widerrufen.
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core'), ENGINE = process.env.PW_BROWSER || 'chromium';
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
const USER2 = process.env.SAGA_USER2, PASS2 = process.env.SAGA_PASS2;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 700))); }

async function session(browser, user, pass, vp) {
  const ctx = await browser.newContext({ viewport: vp || { width: 1366, height: 860 }, locale: 'de-DE', timezoneId: 'Europe/Berlin' });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => { console.log('SEITENFEHLER', e.message); res.push(false); });
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', pass);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(1000);
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, u, b]);
  p.open = async (id, sel) => { await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector(sel || '.tl'); await p.waitForTimeout(700); };
  return p;
}
async function until(p, fn, arg, ms = 15000) {
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
    p = await session(browser, USER, PASS);
    const today = await p.evaluate(() => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); });
    const t0 = new Date(+today.slice(0, 4), +today.slice(5, 7) - 1, +today.slice(8, 10));
    const D = (await p.api('POST', '/api/notes', { kind: 'database', title: 'Zeitleiste Web' })).id; made.push(D);
    let s = (await p.api('GET', '/api/notes/' + D)).schema;
    const st = s.props.find((x) => x.id === 'status');
    s.props = [st, { id: 'beg', name: 'Beginn', type: 'date' }, { id: 'end', name: 'Ende', type: 'date' }, { id: 'zr', name: 'Zeitraum', type: 'date', range: true },
      { id: 'dep', name: 'Blockiert durch', type: 'relation', target: D, two_way: true, role: 'blocked_by' }];
    s.views = [{ id: 'tl', name: 'Gantt', type: 'timeline', date_by: 'beg', end_by: 'end', scale: 'week' },
      { id: 'tl2', name: 'Zeitraum', type: 'timeline', date_by: 'zr', scale: 'day', group: 'status' }];
    const r0 = await p.api('PATCH', '/api/notes/' + D, { schema: s });
    s = (await p.api('GET', '/api/notes/' + D)).schema;
    const v1 = s.views.find((v) => v.id === 'tl'), v2 = s.views.find((v) => v.id === 'tl2');
    ok('Schema: timeline mit end_by/scale gespeichert', r0.ok === true && v1 && v1.type === 'timeline' && v1.end_by === 'end' && v1.scale === 'week' && v2.scale === 'day', s.views);
    const ids = {};
    ids.A = (await p.api('POST', '/api/notes', { parent_id: D, title: 'Planung', props: { status: 'todo', beg: ymd(addDays(t0, -2)), end: ymd(addDays(t0, 3)), zr: ymd(addDays(t0, -2)) + '/' + ymd(addDays(t0, 3)) } })).id;
    ids.B = (await p.api('POST', '/api/notes', { parent_id: D, title: 'Bau', props: { status: 'doing', beg: ymd(addDays(t0, 5)), end: ymd(addDays(t0, 9)), zr: ymd(addDays(t0, 5)) + '/' + ymd(addDays(t0, 9)), dep: [] } })).id;
    ids.C = (await p.api('POST', '/api/notes', { parent_id: D, title: 'Ohne Termin', props: { status: 'todo' } })).id;
    await p.api('PATCH', '/api/notes/' + ids.B, { props: { dep: [ids.A] } });

    await view(p, D, 'tl'); await p.open(D);
    const bars = await p.evaluate(() => [...document.querySelectorAll('.tl-bar')].map((b) => ({ t: b.querySelector('.tl-bt').textContent, w: parseFloat(b.style.width), l: parseFloat(b.style.left) })));
    ok('Woche: zwei Balken, Breite = Tage x 16 px (6 bzw. 5 Tage)', bars.length === 2 && bars.find((b) => b.t === 'Planung').w === 96 && bars.find((b) => b.t === 'Bau').w === 80, bars);
    ok('Achse mit Kalenderwochen, Heute-Linie', await p.evaluate(() => [...document.querySelectorAll('.tl-tick')].some((x) => /^KW \d+/.test(x.textContent)) && !!document.querySelector('.tl-now')));
    ok('Abhaengigkeit als Pfeil (Planung -> Bau)', await p.evaluate(([a, b]) => !!document.querySelector('.tl-dep[data-from="' + a + '"][data-to="' + b + '"]'), [ids.A, ids.B]));
    ok('"ohne Termin" listet die Zeile ohne Datum', await p.evaluate(() => /1 Eintrag ohne Termin/.test((document.querySelector('.tl-nodate summary') || {}).textContent || '') && /Ohne Termin/.test(document.querySelector('.tl-nodate-list').textContent)));

    // Ziehen: Balken "Bau" um 7 Tage (7 x 16 px) nach rechts -> Beginn UND Ende +7
    let bb = await p.locator('.tl-bar[data-id="' + ids.B + '"]').boundingBox();
    await p.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2); await p.mouse.down();
    await p.mouse.move(bb.x + bb.width / 2 + 60, bb.y + bb.height / 2, { steps: 5 }); await p.mouse.move(bb.x + bb.width / 2 + 7 * 16, bb.y + bb.height / 2, { steps: 5 }); await p.mouse.up();
    let okMove = false, g = null;
    for (let i = 0; i < 40 && !okMove; i++) { await p.waitForTimeout(150); g = (await p.api('GET', '/api/notes/' + ids.B)).props; okMove = g.beg === ymd(addDays(t0, 12)) && g.end === ymd(addDays(t0, 16)); }
    ok('Ziehen verschiebt Beginn und Ende (+7 Tage, ein PATCH)', okMove, g);
    // rechter Griff: Ende +2 Tage
    await p.waitForSelector('.tl-bar[data-id="' + ids.B + '"] .tl-h-r');
    bb = await p.locator('.tl-bar[data-id="' + ids.B + '"] .tl-h-r').boundingBox();
    await p.mouse.move(bb.x + 3, bb.y + bb.height / 2); await p.mouse.down(); await p.mouse.move(bb.x + 3 + 2 * 16, bb.y + bb.height / 2, { steps: 6 }); await p.mouse.up();
    okMove = false;
    for (let i = 0; i < 40 && !okMove; i++) { await p.waitForTimeout(150); g = (await p.api('GET', '/api/notes/' + ids.B)).props; okMove = g.beg === ymd(addDays(t0, 12)) && g.end === ymd(addDays(t0, 18)); }
    ok('Griff rechts verlaengert nur das Ende (+2 Tage)', okMove, g);
    // Tastatur: Alt+Pfeil links verschiebt um eine Woche zurueck
    await p.focus('.tl-bar[data-id="' + ids.B + '"]'); await p.keyboard.press('Alt+ArrowLeft');
    okMove = false;
    for (let i = 0; i < 40 && !okMove; i++) { await p.waitForTimeout(150); g = (await p.api('GET', '/api/notes/' + ids.B)).props; okMove = g.beg === ymd(addDays(t0, 5)) && g.end === ymd(addDays(t0, 11)); }
    ok('Tastatur Alt+Pfeil verschiebt um 7 Tage', okMove, g);

    // Skala umschalten: Monat (5 px/Tag) - gespeichert
    await p.click('.tl-scale[data-scale="month"]');
    ok('Skala Monat: Balkenbreite 5 px/Tag, Ansicht gespeichert', await until(p, () => { const b = document.querySelector('.tl-bar'); return b && parseFloat(b.style.width) % 5 === 0 && document.querySelector('.tl-scale[data-scale="month"].on'); })
      && (await p.api('GET', '/api/notes/' + D)).schema.views.find((v) => v.id === 'tl').scale === 'month');
    await p.click('.tl-scale[data-scale="quarter"]');
    ok('Skala Quartal: Beschriftung Q1-Q4', await until(p, () => [...document.querySelectorAll('.tl-tick')].some((x) => /^Q[1-4] \d{4}/.test(x.textContent))));
    await p.click('.tl-nav[data-dir="1"]'); await p.click('.tl-today');

    // Zweite Ansicht: Zeitraum im Datum, Tag-Skala, Gruppen nach Status
    await view(p, D, 'tl2'); await p.open(D);
    const grp = await p.evaluate(() => [...document.querySelectorAll('.tl-group')].map((x) => x.textContent));
    ok('Zeitraum-Datum: Gruppen nach Status, Balken je Zeitraum (Tag = 40 px)', grp.length >= 2 && await p.evaluate(() => { const b = [...document.querySelectorAll('.tl-bar')].find((x) => /Planung/.test(x.textContent)); return b && parseFloat(b.style.width) === 240; }), grp);
    bb = await p.locator('.tl-bar[data-id="' + ids.A + '"] .tl-h-l').boundingBox();
    await p.mouse.move(bb.x + 3, bb.y + bb.height / 2); await p.mouse.down(); await p.mouse.move(bb.x + 3 + 40, bb.y + bb.height / 2, { steps: 6 }); await p.mouse.up();
    okMove = false;
    for (let i = 0; i < 40 && !okMove; i++) { await p.waitForTimeout(150); g = (await p.api('GET', '/api/notes/' + ids.A)).props; okMove = g.zr === ymd(addDays(t0, -1)) + '/' + ymd(addDays(t0, 3)); }
    ok('Zeitraum: Griff links verkuerzt den Beginn (start/ende im selben Wert)', okMove, g);

    // Handy-Breite: keine waagrechte Seitenrolle (Zeitleiste scrollt in sich)
    const ph = await session(browser, USER, PASS, { width: 390, height: 800 });
    await view(ph, D, 'tl'); await ph.open(D);
    ok('Handy: Seite ohne waagrechtes Scrollen, Zeitleiste scrollt innen', await ph.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1 && document.querySelector('.tl-scroll').scrollWidth > document.querySelector('.tl-scroll').clientWidth));
    await ph.context().close();

    // Nur Lesen: kein Ziehen, keine Griffe
    if (USER2) {
      const sh = await p.api('PUT', '/api/notes/' + D + '/shares', { target_type: 'user', target: USER2, role: 'view' });
      const q = await session(browser, USER2, PASS2);
      await view(q, D, 'tl'); await q.open(D);
      ok('Nur Lesen: Balken ohne Griffe, kein "+ Neu"', sh && await q.evaluate(() => document.querySelectorAll('.tl-bar').length >= 1 && !document.querySelector('.tl-h') && !document.querySelector('.tl-new') && !document.querySelector('.tl-bar.tl-edit')));
      await q.context().close();
    }

    // --- Token-UI (Einstellungen -> Konto -> API-Tokens)
    await p.goto(BASE + '/notes'); await p.waitForTimeout(500);
    await p.evaluate(() => window.HKSettings ? window.HKSettings.open('api') : (window.openSettings && window.openSettings('api')));
    await p.waitForSelector('#hk-api-tokens', { timeout: 8000 });
    await p.fill('#hk-api-tokens .api-name', 'Browser-Test');
    await p.check('#hk-api-tokens input[value="read:pages"]');
    await p.click('#hk-api-tokens .api-create');
    // Bestaetigung (Passwort) erscheint, danach Klartext einmal
    await p.waitForSelector('#hk-api-tokens .mfa-confirm input[type=password], #hk-api-tokens .api-newtok', { timeout: 8000 });
    if (await p.$('#hk-api-tokens .mfa-confirm input[type=password]')) {
      await p.fill('#hk-api-tokens .mfa-confirm input[type=password]', PASS);
      await p.click('#hk-api-tokens .mfa-confirm .btn-primary');
    }
    await p.waitForSelector('#hk-api-tokens .api-newtok input', { timeout: 8000 });
    const tok = await p.inputValue('#hk-api-tokens .api-newtok input');
    ok('Token angelegt: Klartext sga_… einmal sichtbar', /^sga_[A-Za-z0-9_-]{40,}$/.test(tok), tok.slice(0, 8));
    const me = await p.evaluate(async (t) => { const r = await fetch('/api/v1/me', { headers: { Authorization: 'Bearer ' + t } }); return [r.status, await r.json()]; }, tok);
    ok('Token funktioniert (/api/v1/me)', me[0] === 200 && me[1].scopes.indexOf('read:pages') >= 0, me);
    const list = await p.evaluate(() => document.querySelector('#hk-api-tokens .api-list').textContent);
    ok('Liste zeigt Name/Praefix, nie den Klartext', /Browser-Test/.test(list) && list.indexOf(tok) < 0 && list.indexOf(tok.slice(0, 12)) >= 0, list.slice(0, 200));
    p.once('dialog', (d) => d.accept());
    await p.click('#hk-api-tokens .api-tok .btn-danger');
    ok('Widerrufen: Token gilt sofort nicht mehr', await until(p, async () => true) && await (async () => {
      for (let i = 0; i < 30; i++) { await p.waitForTimeout(150); const st = await p.evaluate(async (t) => (await fetch('/api/v1/me', { headers: { Authorization: 'Bearer ' + t } })).status, tok); if (st === 401) return true; }
      return false;
    })());
  } catch (e) {
    console.log('ABBRUCH', e.stack || e); res.push(false);
  } finally {
    if (p) for (const id of made) await p.api('DELETE', '/api/notes/' + id).catch(() => {});
    await browser.close();
  }
  const n = res.filter(Boolean).length;
  console.log('\nBROWSER ZEITLEISTE ' + n + '/' + res.length + (n === res.length && n ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(n === res.length && n ? 0 : 1);
})();
