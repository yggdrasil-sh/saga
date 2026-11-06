// Saga 2.5 (chats1) - Chat-Liste in der Seitenleiste: auf der Chats-Seite (breit) steht die Liste als Abschnitt "Chats" in der
// globalen Seitenleiste, die Unterhaltung hat die volle Breite; schmal (Handy) und im Panel bleibt die Listenspalte.
// SAGA_BASE, SAGA_USER/SAGA_PASS, SAGA_USER2/SAGA_PASS2 (zwei frische Konten; Chat-Schluessel richtet der Test ein).
// SHOTS=/pfad/ordner legt Bildschirmfotos ab (dunkel/hell).
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const fs = require('fs'); const E = process.env; const BASE = E.SAGA_BASE || 'http://127.0.0.1:8099';
const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
const RK = u => require('os').tmpdir() + '/saga-browser-rk-' + BASE.replace(/[^a-z0-9]+/gi, '_') + '-' + u + '.txt';
async function login(br, u, opts = {}) {
  const ctx = await br.newContext(Object.assign({ viewport: { width: 1440, height: 880 }, locale: 'de-DE' }, opts));
  const p = await ctx.newPage(); p.on('pageerror', e => console.log('[err ' + u + ']', e.message));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', u); await p.fill('input[name=password]', u === U2 ? E.SAGA_PASS2 : E.SAGA_PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]); return p;
}
async function keys(p, u) {
  await p.waitForTimeout(1500);
  if (await p.$('.ct-setup-go')) {
    await p.click('.ct-setup-go'); await p.waitForSelector('#ct-rk'); const rk = (await p.textContent('#ct-rk')).trim(); fs.writeFileSync(RK(u), rk);
    await p.click('.hk-actions .btn-primary'); await p.fill('.ct-rk-last', rk.slice(-4)); await p.click('.ct-setup-done');
    await p.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 60000 });
  } else if (await p.$('.ct-rk-go')) {
    await p.click('.ct-rk-go'); await p.fill('.ct-rk-in', fs.readFileSync(RK(u), 'utf8')); await p.click('.ct-rk-ok');
    await p.waitForSelector('.ct-rk-ok', { state: 'detached', timeout: 60000 });
  }
  await p.waitForTimeout(600);
}
async function send(p, text) { await p.fill('#ct-input', text); await p.press('#ct-input', 'Enter'); await p.waitForTimeout(700); }
const R = []; const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x || ''))); };
const inSide = p => p.evaluate(() => { const s = document.getElementById('ct-side'); return !!(s && s.closest('#sh-chats')); });
const inGrid = p => p.evaluate(() => { const s = document.getElementById('ct-side'); return !!(s && s.parentNode && s.parentNode.id === 'ct'); });

(async () => {
  const br = await chromium.launch();
  const a = await login(br, U1, { colorScheme: 'dark' }), b = await login(br, U2);
  await a.goto(BASE + '/chats'); await keys(a, U1); await b.goto(BASE + '/chats'); await keys(b, U2);
  await a.goto(BASE + '/chats#u' + U2); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200); await send(a, 'Hallo!');
  await b.waitForTimeout(1500);

  // --- breit: Liste in der Seitenleiste ---
  await a.goto(BASE + '/chats'); await a.waitForSelector('#ct-list .ct-item-b', { timeout: 30000 }); await a.waitForTimeout(500);
  ok('breit: Chat-Liste steckt in der Seitenleiste', await inSide(a));
  ok('breit: nicht mehr als Spalte im Raster', !(await inGrid(a)));
  ok('breit: Klasse chats-in-side', await a.evaluate(() => document.body.classList.contains('chats-in-side')));
  ok('breit: Abschnitt "Chats" sichtbar', await a.isVisible('#sh-chats-h') && await a.isVisible('#sh-chats'));
  ok('breit: keine zweite Chats-Zeile in der Navigation', await a.evaluate(() => !document.querySelector('#pg-mainnav a[href="/chats"]')));
  ok('breit: Eintrag sichtbar', await a.isVisible('#sh-chats .ct-item-b'));
  const cols = await a.evaluate(() => getComputedStyle(document.getElementById('ct')).gridTemplateColumns.split(' ').length);
  ok('breit: Raster hat nur eine Spalte', cols === 1, cols);
  await a.click('#sh-chats .ct-item-b'); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(800);
  const geo = await a.evaluate(() => { const c = document.getElementById('ct-conv').getBoundingClientRect(), s = document.getElementById('pg-side').getBoundingClientRect(), m = document.querySelector('main.ct-main').getBoundingClientRect();
    return { convL: c.left, convW: c.width, sideR: s.right, mainW: m.width, vw: innerWidth }; });
  ok('breit: Unterhaltung beginnt direkt neben der Seitenleiste, volle Breite', Math.abs(geo.convL - geo.sideR) < 4 && Math.abs(geo.convW - geo.mainW) < 4, geo);
  ok('breit: Eintrag ist markiert', await a.isVisible('#sh-chats .ct-item.on'));
  ok('breit: Hash springt auf die Unterhaltung', /#c\d+/.test(await a.evaluate(() => location.hash)));
  if (E.SHOTS) { fs.mkdirSync(E.SHOTS, { recursive: true }); await a.screenshot({ path: E.SHOTS + '/chats-breit-dunkel.png' }); }

  // --- Neu-Knopf und Filter wirken noch ---
  await a.click('#ct-new'); await a.waitForTimeout(500);
  ok('breit: "Neu" oeffnet den Dialog', await a.evaluate(() => !!document.querySelector('.hk-dlg, .hk-modal, dialog[open], #ct-picker:not([hidden]), .ct-new-dlg')) || (await a.$$('input')).length > 1);
  await a.keyboard.press('Escape'); await a.waitForTimeout(300);
  await a.fill('#ct-filter', 'zzzz-nichts'); await a.waitForTimeout(300);
  ok('Filter blendet nicht passende Eintraege aus', (await a.$$('#sh-chats .ct-item')).length === 0);
  await a.fill('#ct-filter', ''); await a.waitForTimeout(300);
  ok('Filter leeren zeigt sie wieder', (await a.$$('#sh-chats .ct-item')).length >= 1);

  // --- Seitenleiste ein/aus: Kopf der Unterhaltung nicht unter dem Knopf ---
  await a.keyboard.press('Control+\\'); await a.waitForTimeout(500);
  const pad = await a.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.ct-head')).paddingLeft));
  ok('Seitenleiste zu: Kopf rueckt vom Knopf weg', pad >= 48, pad);
  ok('Seitenleiste zu: Unterhaltung nutzt die ganze Breite', await a.evaluate(() => document.getElementById('ct-conv').getBoundingClientRect().left < 8));
  await a.keyboard.press('Control+\\'); await a.waitForTimeout(500);

  // --- Fenstergroesse wechseln: Liste wandert mit ---
  await a.setViewportSize({ width: 600, height: 800 }); await a.waitForTimeout(500);
  ok('schmal: Liste wieder in der Spalte', await inGrid(a) && !(await inSide(a)));
  ok('schmal: Klasse chats-in-side weg', await a.evaluate(() => !document.body.classList.contains('chats-in-side')));
  await a.setViewportSize({ width: 1440, height: 880 }); await a.waitForTimeout(500);
  ok('wieder breit: Liste in der Seitenleiste', await inSide(a));
  ok('wieder breit: Eintraege noch da', (await a.$$('#sh-chats .ct-item')).length >= 1);

  // --- Handy von Anfang an ---
  const m = await login(br, U1, { viewport: { width: 390, height: 800 }, colorScheme: 'light' });
  await m.goto(BASE + '/chats'); await m.waitForSelector('#ct-list .ct-item-b', { timeout: 30000 }).catch(() => {});
  if (await m.$('.ct-rk-go')) await keys(m, U1);
  await m.waitForTimeout(800);
  ok('Handy: Listenspalte wie bisher', await inGrid(m) && !(await inSide(m)));
  ok('Handy: Liste sichtbar', await m.isVisible('#ct-list .ct-item-b'));
  if (E.SHOTS) await m.screenshot({ path: E.SHOTS + '/chats-handy.png' });

  // --- Panel unveraendert ---
  await a.goto(BASE + '/chats?panel=1'); await a.waitForTimeout(1500);
  if (await a.$('.ct-rk-go')) await keys(a, U1);
  ok('Panel: Listenspalte wie bisher', await inGrid(a));
  ok('Panel: keine Seitenleiste', !(await a.$('#pg-side')));

  // --- Heute/Seiten: Chats-Zeile bleibt ---
  await a.goto(BASE + '/dashboard'); await a.waitForTimeout(800);
  ok('Heute: Chats-Zeile in der Navigation', await a.evaluate(() => !!document.querySelector('#pg-mainnav a[href="/chats"]')));
  ok('Heute: kein Chat-Abschnitt', !(await a.$('#sh-chats')));

  // --- hell ---
  const l = await login(br, U1, { colorScheme: 'light' });
  await l.goto(BASE + '/chats#u' + U2); await l.waitForSelector('#ct-chat:not([hidden])', { timeout: 30000 }).catch(() => {});
  if (await l.$('.ct-rk-go')) { await keys(l, U1); await l.goto(BASE + '/chats#u' + U2); }
  await l.waitForTimeout(1500);
  if (E.SHOTS) await l.screenshot({ path: E.SHOTS + '/chats-breit-hell.png' });

  await br.close();
  const bad = R.filter(x => !x[1]); console.log(`\n${R.length - bad.length}/${R.length} ok`); process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
