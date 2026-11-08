// BookOrbit (2.5, Phase 1) im Web: Einstellungen -> Verbindungen -> BookOrbit (anmelden, Status, Trennen), Widget
// "Lesen" auf einer Seite (Gerade am Lesen mit Cover ueber Saga, Leseziel, Lesestraehne; Hinweis ohne Verbindung) und die
// Karte "Lesen" unter "Heute" (nur sichtbar, wenn verbunden). Der Browser spricht nie mit BookOrbit selbst.
// Ein kleines Fake-BookOrbit laeuft in diesem Skript auf 127.0.0.1:BOOKORBIT_TEST_PORT (18645); der Server muss mit
// BOOKORBIT_URL=http://127.0.0.1:18645 laufen (Vorschlag = automatisch freigegeben).
//   HK_TEST_BASE=http://127.0.0.1:18640 CK=<Cookie> [SHOTS=/tmp/bookorbit] node tests/browser_bookorbit.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const http = require('http'), crypto = require('crypto'), fs = require('fs');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, PORT = +(process.env.BOOKORBIT_TEST_PORT || 18645), SHOTS = process.env.SHOTS || '';
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(150); } }

// --- Fake-BookOrbit ------------------------------------------------------------------------------------------------
const PASS = 'Web-' + crypto.randomBytes(5).toString('hex');
const tokens = new Set(), access = new Set();
// 1x1-PNG (blau)
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
function creds() {
  const rt = crypto.randomBytes(32).toString('hex'), at = 'acc-' + crypto.randomBytes(8).toString('hex');
  tokens.add(rt); access.add(at);
  const iso = (s) => new Date(Date.now() + s * 1000).toISOString();
  return { accessToken: at, accessTokenExpiresAt: iso(900), refreshToken: rt, refreshTokenExpiresAt: iso(7 * 86400), sessionId: 1 };
}
const fake = http.createServer((q, r) => {
  let b = ''; q.on('data', (c) => { b += c; }); q.on('end', () => {
    const send = (code, body, ct) => { r.writeHead(code, { 'Content-Type': ct || 'application/json' }); r.end(Buffer.isBuffer(body) ? body : JSON.stringify(body)); };
    let d = {}; try { d = JSON.parse(b || '{}'); } catch (e) { d = {}; }
    const authed = access.has((q.headers.authorization || '').slice(7));
    if (q.url === '/api/v1/auth/login') return d.username === 'leserin' && d.password === PASS && d.clientKind === 'native'
      ? send(200, Object.assign(creds(), { user: { username: 'leserin', name: 'Lea Leserin' } })) : send(401, { message: 'Invalid credentials' });
    if (q.url === '/api/v1/auth/refresh') { if (!tokens.has(d.refreshToken)) return send(401, {}); tokens.delete(d.refreshToken); return send(200, creds()); }
    if (q.url === '/api/v1/auth/logout') { tokens.delete(d.refreshToken); return send(200, {}); }
    if (!authed) return send(401, { message: 'Unauthorized' });
    if (q.url === '/api/v1/dashboard/widgets/batch') return send(200, { items: [
      { type: 'currently-reading', failed: false, data: { books: [
        { bookId: 7, title: 'Die Bibliothek im Nebel', authors: ['Mara Wendt'], progress: 63.2, hasCover: true, fileFormat: 'EPUB' },
        { bookId: 8, title: 'Kleine Fluchten', authors: ['Jon Arve'], progress: 12, hasCover: false, fileFormat: 'PDF' }] } },
      { type: 'reading-goal', failed: false, data: { goalBooks: 24, completedBooks: 17, year: 2026 } },
      { type: 'reading-streak', failed: false, data: { currentStreak: 6, longestStreak: 21, lastSevenDays: [false, true, true, true, true, true, true] } }] });
    if (/^\/api\/v1\/books\/7\/thumbnail$/.test(q.url)) return send(200, PNG, 'image/png');
    send(404, { message: 'Not Found' });
  });
});

(async () => {
  await new Promise((ok_) => fake.listen(PORT, '127.0.0.1', ok_));
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [], ext = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused/.test(m.text())) errs.push(m.text()); });
  p.on('request', (r) => { const h = new URL(r.url()).host; if (h !== new URL(BASE).host && !/^(data|blob):/.test(r.url())) ext.push(r.url()); });
  await p.goto(BASE + '/dashboard');
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  await api('POST', '/api/bookorbit/disconnect');
  await p.goto(BASE + '/dashboard'); await sleep(1200);
  ok('Heute: Karte "Lesen" ohne Verbindung verborgen', await p.locator('#hz-reading').isHidden());

  // --- Seite mit Widget, noch ohne Verbindung: Hinweis mit Weg zu den Einstellungen ----------------------------------
  const W = (bid, cfg, extra) => Object.assign({ id: bid, type: 'widget', data: Object.assign({ v: 1, kind: 'reading', cfg: cfg }, extra || {}) });
  const id = (await api('POST', '/api/notes', { title: 'BO Leseecke' })).id;
  await api('PATCH', '/api/notes/' + id, { format: 'blocks', body: JSON.stringify({ blocks: [
    { type: 'columns', data: { cols: [
      { blocks: [W('rc', { show: 'current', limit: 2 }, { title: 'Gerade am Lesen' })] },
      { blocks: [W('rg', { show: 'goal' }, { title: 'Leseziel' }), W('rs', { show: 'streak' }, { title: 'Lesesträhne', size: 's' })] }] } },
    { type: 'paragraph', data: { text: '' } }] }) });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
  const hint = await until(async () => { const t = await p.locator('.wg[data-kind="reading"]').first().textContent().catch(() => ''); return /Noch nicht mit BookOrbit verbunden/.test(t) ? t : null; }, 8000);
  ok('Widget ohne Verbindung: freundlicher Hinweis', !!hint, hint);
  if (SHOTS) await p.screenshot({ path: SHOTS + '/widget-ohne-verbindung.png' });
  // Hinweis-Knopf oeffnet die Einstellungen beim Bereich BookOrbit
  await p.locator('.wg[data-kind="reading"] .bo-hint .linkbtn').first().click();
  await until(() => p.locator('#hk-bo-user').count(), 5000);
  ok('Knopf "Einstellungen öffnen" fuehrt zu BookOrbit', await p.locator('#hk-bo-user').isVisible());
  ok('Adresse vorgeschlagen (BOOKORBIT_URL)', (await p.locator('#hk-bo-url').inputValue()) === 'http://127.0.0.1:' + PORT);
  await p.fill('#hk-bo-user', 'leserin'); await p.fill('#hk-bo-pw', 'falsch'); await p.click('#hk-bo-go');
  const err = await until(async () => { const t = await p.locator('.hk-set-bookorbit').textContent(); return /lehnt/.test(t) ? t : null; }, 6000);
  ok('falsches Passwort: Meldung im Formular', !!err);
  ok('Passwortfeld nach Fehlversuch geleert', (await p.locator('#hk-bo-pw').inputValue()) === '');
  await p.fill('#hk-bo-pw', PASS); await p.click('#hk-bo-go');
  const con = await until(async () => { const t = await p.locator('.hk-set-bookorbit').textContent(); return /Verbunden als Lea Leserin/.test(t) ? t : null; }, 6000);
  ok('verbunden: "Verbunden als Lea Leserin"', !!con, con);
  if (SHOTS) await p.locator('.hk-dialog-set').screenshot({ path: SHOTS + '/einstellungen-verbunden.png' });
  await p.keyboard.press('Escape'); await sleep(300);

  // --- Widget mit Daten ----------------------------------------------------------------------------------------------
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
  const cur = await until(async () => { const t = await p.locator('.wg[data-kind="reading"]').first().textContent().catch(() => ''); return /Bibliothek im Nebel/.test(t) ? t : null; }, 8000);
  ok('Widget "Gerade am Lesen": Titel, Autorin, 63 %', !!cur && /Mara Wendt/.test(cur) && /63 %/.test(cur) && /Kleine Fluchten/.test(cur), cur);
  const img = await until(() => p.evaluate(() => { const i = document.querySelector('.wg[data-kind="reading"] .bo-cover img'); return i && i.complete && i.naturalWidth > 0 ? i.src : null; }), 6000);
  ok('Cover geladen (Blob ueber den Saga-Server)', !!img && /^blob:/.test(img), img);
  const goal = await p.locator('.wg[data-kind="reading"]').nth(1).textContent();
  ok('Leseziel: 17 / 24', /17 \/ 24/.test(goal), goal);
  const st = await p.locator('.wg[data-kind="reading"]').nth(2).textContent();
  ok('Lesesträhne: 6 Tage, längste 21', /6 Tage/.test(st) && /21/.test(st), st);
  ok('sieben Tagespunkte, sechs gelesen', await p.locator('.wg[data-kind="reading"] .bo-day').count() === 7 && await p.locator('.wg[data-kind="reading"] .bo-day.bo-on').count() === 6);
  await sleep(400);
  if (SHOTS) await p.screenshot({ path: SHOTS + '/widget-lesen.png' });
  // Einrichten: Variante umstellen (Panel)
  await p.locator('.wg[data-kind="reading"]').nth(2).click();
  const panel = await until(() => p.locator('.wg-panel').count(), 4000);
  ok('Einrichten: Panel geht auf', !!panel);
  if (panel) {
    const s = p.locator('.wg-panel .wg-row', { has: p.locator('.wg-l', { hasText: /^Anzeigen$/ }) }).locator('select');
    ok('Varianten: Gerade am Lesen, Leseziel, Lesesträhne', JSON.stringify(await s.locator('option').allTextContents()) === JSON.stringify(['Gerade am Lesen', 'Leseziel', 'Lesesträhne']));
    if (SHOTS) await p.screenshot({ path: SHOTS + '/widget-einrichten.png' });
    await p.locator('.wg-panel .wg-done').click();
  }

  // --- Heute ---------------------------------------------------------------------------------------------------------
  await p.goto(BASE + '/dashboard');
  const hz = await until(async () => (await p.locator('#hz-reading').isVisible()) && /Bibliothek im Nebel/.test(await p.locator('#hz-reading').textContent()), 6000);
  ok('Heute: Karte "Lesen" mit dem aktuellen Buch', !!hz);
  ok('Heute: Strähne als Zeile', /6 Tage in Folge gelesen/.test(await p.locator('#hz-reading').textContent()));
  await until(() => p.evaluate(() => { const i = document.querySelector('#hz-reading .bo-cover img'); return i && i.complete && i.naturalWidth > 0; }), 5000);
  if (SHOTS) await p.screenshot({ path: SHOTS + '/heute-lesen.png', fullPage: false });
  await p.setViewportSize({ width: 390, height: 800 }); await sleep(500);
  const ov = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  ok(`Handy-Breite: kein seitliches Scrollen (${ov})`, ov <= 2);
  if (SHOTS) await p.locator('#hz-reading').screenshot({ path: SHOTS + '/heute-lesen-handy.png' });
  await p.setViewportSize({ width: 1280, height: 900 });

  // --- Trennen -------------------------------------------------------------------------------------------------------
  await p.evaluate(() => window.HKSettings.open('bookorbit'));
  await until(() => p.locator('#hk-bo-off').count(), 4000);
  await p.click('#hk-bo-off');
  const off = await until(() => p.locator('#hk-bo-user').count(), 5000);
  ok('Trennen: Formular wieder da', !!off);
  ok('Trennen: bei BookOrbit abgemeldet (kein gueltiges Token mehr)', tokens.size === 0, tokens.size);
  await p.keyboard.press('Escape');
  await p.goto(BASE + '/dashboard'); await sleep(1200);
  ok('Heute: Karte nach dem Trennen verborgen', await p.locator('#hz-reading').isHidden());

  ok('Browser spricht nie direkt mit BookOrbit', !ext.length, ext.slice(0, 3));
  ok('Keine Seitenfehler / CSP-Verstoesse', !errs.length, errs);
  await api('DELETE', '/api/notes/' + id);
  await br.close(); fake.close();
  const g = res.filter(Boolean).length;
  console.log('\nBROWSER-BOOKORBIT ' + g + '/' + res.length + (g === res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(g === res.length ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
