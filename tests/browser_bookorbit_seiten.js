// BookOrbit Phase 2 (2.5) im Web: Einstellungen -> Verbindungen -> BookOrbit -> "Markierungen als Seite übernehmen"
// (Liste mit Anzahl, "Als Seite" legt "Notizen: <Titel>" an und oeffnet sie), eigene Ergaenzung im Editor bleibt,
// Seitenmenue "Aus BookOrbit aktualisieren" holt nur Neues (Bloecke behalten ihre BookOrbit-Kennung durch den Editor),
// Buch antippen unter "Heute" und im Widget "Lesen" -> Menue "Markierungen als Seite". Der Browser spricht nie mit BookOrbit.
// Ein kleines Fake-BookOrbit laeuft in diesem Skript auf 127.0.0.1:BOOKORBIT_TEST_PORT (18645); der Server muss mit
// BOOKORBIT_URL=http://127.0.0.1:<port> laufen (Vorschlag = automatisch freigegeben).
//   HK_TEST_BASE=http://127.0.0.1:18640 CK=<Cookie> [SHOTS=/tmp/bookorbit] node tests/browser_bookorbit_seiten.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const http = require('http'), crypto = require('crypto'), fs = require('fs');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, PORT = +(process.env.BOOKORBIT_TEST_PORT || 18645), SHOTS = process.env.SHOTS || '';
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(150); } }

// --- Fake-BookOrbit ------------------------------------------------------------------------------------------------
const PASS = 'Web-' + crypto.randomBytes(5).toString('hex');
const tokens = new Set(), access = new Set();
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
const ann = (id, text, ch, note, color) => ({ id: id, bookId: 7, cfi: null, jumpFileId: null, pageno: id - 7000, text: text, color: color || '#FACC15',
  style: 'highlight', note: note || null, chapterTitle: ch, origin: 'web', positionStatus: 'exact', chapterIndex: null,
  highlightedAt: '2026-10-1' + (id % 10) + 'T09:00:00.000Z', createdAt: '2026-10-10T09:00:00.000Z', starredAt: null });
const ANN = [ann(7001, 'Der Nebel kam ueber die Regale.', 'Erstes Kapitel', null, '#4ADE80'),
  ann(7002, 'Jedes Buch hat eine eigene Stille.', 'Zweites Kapitel', 'Das merke ich mir')];
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
    const u = new URL(q.url, 'http://x'), authed = access.has((q.headers.authorization || '').slice(7));
    if (u.pathname === '/api/v1/auth/login') return d.username === 'leserin' && d.password === PASS && d.clientKind === 'native'
      ? send(200, Object.assign(creds(), { user: { username: 'leserin', name: 'Lea Leserin' } })) : send(401, { message: 'Invalid credentials' });
    if (u.pathname === '/api/v1/auth/refresh') { if (!tokens.has(d.refreshToken)) return send(401, {}); tokens.delete(d.refreshToken); return send(200, creds()); }
    if (u.pathname === '/api/v1/auth/logout') { tokens.delete(d.refreshToken); return send(200, {}); }
    if (!authed) return send(401, { message: 'Unauthorized' });
    if (u.pathname === '/api/v1/dashboard/widgets/batch') return send(200, { items: [
      { type: 'currently-reading', failed: false, data: { books: [{ bookId: 7, title: 'Die Bibliothek im Nebel', authors: ['Mara Wendt'], progress: 63.2, hasCover: true, fileFormat: 'EPUB' }] } },
      { type: 'reading-goal', failed: false, data: { goalBooks: 24, completedBooks: 17, year: 2026 } },
      { type: 'reading-streak', failed: false, data: { currentStreak: 6, longestStreak: 21, lastSevenDays: [true, true, true, true, true, true, true] } }] });
    if (u.pathname === '/api/v1/annotations/books') return send(200, [{ bookId: 7, bookTitle: 'Die Bibliothek im Nebel', author: 'Mara Wendt', count: ANN.length }]);
    if (u.pathname === '/api/v1/books/7') return send(200, { id: 7, title: 'Die Bibliothek im Nebel', authors: [{ id: 1, name: 'Mara Wendt', sortName: null }], personalNote: null });
    if (u.pathname === '/api/v1/books/7/annotations') {
      const pg = +(u.searchParams.get('page') || 1), sz = +(u.searchParams.get('pageSize') || 25);
      return send(200, { items: ANN.slice((pg - 1) * sz, pg * sz), total: ANN.length, page: pg, pageSize: sz, stats: { chapters: [] } });
    }
    if (u.pathname === '/api/v1/books/7/bookmarks') return send(200, []);
    if (u.pathname === '/api/v1/books/7/thumbnail') return send(200, PNG, 'image/png');
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
  const con = await api('POST', '/api/bookorbit/connect', { url: 'http://127.0.0.1:' + PORT, username: 'leserin', password: PASS });
  ok('verbunden', con && con.ok, con);

  // --- Einstellungen -> BookOrbit -> Liste ---------------------------------------------------------------------------
  await p.evaluate(() => window.HKSettings.open('bookorbit'));
  const row = await until(async () => { const t = await p.locator('#hk-bo-notes').textContent().catch(() => ''); return /Bibliothek im Nebel/.test(t) ? t : null; }, 8000);
  ok('Einstellungen: Liste "Markierungen als Seite übernehmen" mit Buch und Anzahl', !!row && /Markierungen als Seite übernehmen/.test(row)
     && /Mara Wendt · 2 Markierungen/.test(row), row);
  await until(() => p.evaluate(() => { const i = document.querySelector('#hk-bo-notes .bo-cover img'); return i && i.complete && i.naturalWidth > 0; }), 5000);
  if (SHOTS) await p.locator('.hk-dialog-set').screenshot({ path: SHOTS + '/einstellungen-markierungen.png' });
  await p.locator('#hk-bo-notes .bo-nl-go').first().click();
  await until(() => /\/notes/.test(p.url()) && p.locator('#pg-title').isVisible().catch(() => false), 10000);
  const title = await until(async () => { const v = await p.locator('#pg-title').inputValue().catch(() => ''); return /Notizen:/.test(v) ? v : null; }, 10000);
  ok('"Als Seite": Seite "Notizen: Die Bibliothek im Nebel" geoeffnet', title === 'Notizen: Die Bibliothek im Nebel', title);
  const id = +(p.url().split('#')[1] || 0);
  const shown = await until(async () => { const t = await p.locator('#pg-editor').textContent().catch(() => ''); return /Nebel kam/.test(t) && /eigene Stille/.test(t) ? t : null; }, 8000);
  ok('Editor zeigt Kapitel, Zitate und Notiz', !!shown && /Erstes Kapitel/.test(shown) && /Das merke ich mir/.test(shown), shown && shown.slice(0, 300));
  ok('Editor zeigt Zitat-Bloecke', await p.locator('#pg-editor [data-block-type="quote"], #pg-editor .cdx-quote').count() >= 2);
  if (SHOTS) await p.screenshot({ path: SHOTS + '/seite-markierungen.png' });

  // --- Eigene Ergaenzung im Editor, dann "Aus BookOrbit aktualisieren" -------------------------------------------------
  const ed = (await p.locator('#pg-editor .ProseMirror').count()) ? '#pg-editor .ProseMirror' : '#pg-editor [contenteditable=true]';
  await p.locator(ed).last().click();
  await p.keyboard.press('Control+End'); await p.keyboard.press('End'); await p.keyboard.press('Enter');
  await p.keyboard.type('Mein eigener Gedanke zum Buch');
  const saved = await until(async () => { const n = await api('GET', '/api/notes/' + id); return /Mein eigener Gedanke/.test(n.body || '') ? n : null; }, 10000);
  ok('eigene Ergaenzung gespeichert', !!saved);
  ok('Editor behaelt die BookOrbit-Kennung der Bloecke (data.bo)', !!saved && /"a":\s*7001/.test(saved.body) && /"a":\s*7002/.test(saved.body), saved && saved.body.slice(0, 400));
  ANN.splice(1, 0, ann(7003, 'Neu markiert im ersten Kapitel.', 'Erstes Kapitel'));
  await p.click('#pg-more');
  const item = p.locator('.menu:not([hidden]) .menu-item', { hasText: 'Aus BookOrbit aktualisieren' });
  ok('Seitenmenue: "Aus BookOrbit aktualisieren"', await until(() => item.count(), 3000));
  await item.first().click();
  const upd = await until(async () => { const t = await p.locator('#pg-editor').textContent().catch(() => ''); return /Neu markiert/.test(t) ? t : null; }, 10000);
  ok('nach dem Aktualisieren: neue Markierung da, eigene Ergaenzung bleibt', !!upd && /Mein eigener Gedanke/.test(upd), upd && upd.slice(0, 400));
  const order = upd ? [upd.indexOf('Nebel kam'), upd.indexOf('Neu markiert'), upd.indexOf('Zweites Kapitel')] : [];
  ok('neue Markierung im ersten Kapitel (vor "Zweites Kapitel")', order.length && order[0] < order[1] && order[1] < order[2], order);
  const again = await api('POST', '/api/bookorbit/notes', { page_id: id });
  ok('noch einmal: nichts Neues (idempotent nach Editor-Speichern)', again.added === 0 && again.updated === 0, again);
  const tm = await until(async () => { const t = await p.locator('.hk-toast, .toast').last().textContent().catch(() => ''); return t || null; }, 2000);
  ok('Meldung nennt das Ergebnis', !!tm, tm);

  // --- Heute: Buch antippen -> Menue -> Markierungen als Seite -----------------------------------------------------------
  await p.goto(BASE + '/dashboard');
  await until(async () => (await p.locator('#hz-reading').isVisible()) && /Bibliothek im Nebel/.test(await p.locator('#hz-reading').textContent()), 8000);
  ok('Heute: Buch ist antippbar (Rolle button)', await p.locator('#hz-reading .bo-book.bo-tap[role="button"]').count() === 1);
  await p.locator('#hz-reading .bo-book.bo-tap').first().click();
  const mi = p.locator('.menu:not([hidden]) .menu-item', { hasText: 'Markierungen als Seite' });
  ok('Heute: Menue "Markierungen als Seite"', await until(() => mi.count(), 3000));
  if (SHOTS) await p.screenshot({ path: SHOTS + '/heute-menue.png' });
  await mi.first().click();
  await until(() => /\/notes#/.test(p.url()), 10000);
  ok('Heute: fuehrt zur selben Seite', +(p.url().split('#')[1] || 0) === id, p.url());

  // --- Widget "Lesen": Buch antippen -> Menue ---------------------------------------------------------------------------
  const wid = (await api('POST', '/api/notes', { title: 'BO Widget' })).id;
  await api('PATCH', '/api/notes/' + wid, { format: 'blocks', body: JSON.stringify({ blocks: [{ id: 'rc', type: 'widget', data: { v: 1, kind: 'reading', cfg: { show: 'current', limit: 2 } } }] }) });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + wid);
  await until(async () => /Bibliothek im Nebel/.test(await p.locator('.wg[data-kind="reading"]').first().textContent().catch(() => '')), 8000);
  await p.locator('.wg[data-kind="reading"] .bo-book.bo-tap').first().click();
  ok('Widget: Buch antippen -> Menue "Markierungen als Seite"', await until(() => mi.count(), 3000));
  ok('Widget: dabei kein Einrichten-Panel', (await p.locator('.wg-panel').count()) === 0);
  await p.keyboard.press('Escape');

  ok('Browser spricht nie direkt mit BookOrbit', !ext.length, ext.slice(0, 3));
  ok('Keine Seitenfehler / CSP-Verstoesse', !errs.length, errs);
  await api('DELETE', '/api/notes/' + wid); await api('DELETE', '/api/notes/' + id);
  await api('POST', '/api/bookorbit/disconnect');
  await br.close(); fake.close();
  const g = res.filter(Boolean).length;
  console.log('\nBROWSER-BOOKORBIT-SEITEN ' + g + '/' + res.length + (g === res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(g === res.length ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
