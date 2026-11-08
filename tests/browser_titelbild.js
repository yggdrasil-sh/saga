const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
// Titelbild-Galerie (2.5): neue Seite zeigt ein zufaelliges Galerie-Titelbild; Titelbild-Menue -> "Galerie …" (Gruppen,
// Vorschaustreifen), Bild waehlen -> sichtbar und gespeichert; "Über dieses Bild"; "Zufällig"; Einstellung in den
// Einstellungen. Optional Admin -> Titelbilder (Vorschau der Position per Schieberegler, keine CSP-Fehler).
//   SAGA_BASE=… SAGA_USER=konto SAGA_PASS=… [SAGA_ADMIN=http://…:8081 SAGA_ADMIN_COOKIE=<hk_admin-Wert>] node tests/browser_titelbild.js
const BASE = process.env.SAGA_BASE || process.env.HK_TEST_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
const ADMIN = process.env.SAGA_ADMIN, ACK = process.env.SAGA_ADMIN_COOKIE;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const bad = []; let n = 0;
const ok = (c, m) => { n++; if (!c) { bad.push(m); console.log('  FEHLT: ' + m); } else console.log('  ok: ' + m); };
const wait = ms => new Promise(r => setTimeout(r, ms));
async function until(f, ms) { const t0 = Date.now(); while (Date.now() - t0 < (ms || 5000)) { if (await f()) return true; await wait(100); } return false; }
(async () => {
  const b = await chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await b.newContext({ viewport: { width: 1400, height: 900 }, locale: 'de-DE' });
  const p = await ctx.newPage(); const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  const api = (m, url, body) => p.evaluate(async ([m, url, body]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const t = await r.text(); try { return JSON.parse(t); } catch (e) { return t; }
  }, [m, url, body]);
  const coverShown = () => p.evaluate(() => { const c = document.getElementById('pg-cover'), i = document.getElementById('pg-cover-img');
    return !c.hidden && i.complete && i.naturalWidth > 0 ? i.getAttribute('src') : ''; });
  const open = async id => {
    await p.goto(BASE + '/notes#' + id);
    await until(() => p.evaluate(i => !document.getElementById('page').hidden && window.location.hash === '#' + i, id));
    await wait(600);
  };

  console.log('Neue Seite');
  await api('PUT', '/api/account/pages', { random_cover: true });
  const r1 = await api('POST', '/api/notes', { title: 'Galerie zufällig ' + String(Date.now()).slice(-5) });
  await open(r1.id);
  ok(await until(async () => /^\/covers\/[a-z0-9-]+\.jpg$/.test(await coverShown())), 'neue Seite zeigt ein Galerie-Titelbild (' + await coverShown() + ')');

  console.log('Galerie-Auswahl');
  const id = (await api('POST', '/api/notes', { title: 'Galerie wählen ' + String(Date.now()).slice(-5), cover: null })).id;
  await open(id);
  ok(await p.evaluate(() => document.getElementById('pg-cover').hidden && !document.getElementById('pg-addcover').hidden), 'Seite ohne Titelbild: „Titelbild“ angeboten');
  await p.evaluate(() => document.getElementById('pg-addcover').click()); await wait(250);
  const items = await p.evaluate(() => [...document.querySelectorAll('.menu .menu-item .mi-t')].map(x => x.textContent));
  ok(items[0] === 'Galerie …' && items.includes('Zufällig') && items.includes('Bild hochladen …'), 'Menue: Galerie, Zufällig, Bild hochladen (' + items.join(' · ') + ')');
  ok(await p.evaluate(() => !!document.querySelector('.menu .menu-item .mi-ic svg')), 'Menue mit Liniensymbolen');
  await p.locator('.menu .menu-item:has(.mi-t:text-is("Galerie …"))').click();
  ok(await until(() => p.evaluate(() => !document.getElementById('pg-modal').hidden && document.querySelectorAll('.cg-item').length >= 20)), 'Dialog mit Vorschaustreifen');
  const gs = await p.evaluate(() => [...document.querySelectorAll('.cg-h')].map(x => x.textContent));
  ok(gs.slice(0, 4).join(',') === 'Nordisch,Romantik,Holzschnitt,Landschaft', 'Gruppen in Reihenfolge (' + gs.join(', ') + ')');
  ok(await p.evaluate(() => document.getElementById('pg-modal-title').textContent === 'Titelbild-Galerie'), 'Titel „Titelbild-Galerie“');
  if (process.env.SHOTS) { await wait(800); await p.screenshot({ path: process.env.SHOTS + '/galerie.png' }); }
  const wave = p.locator('.cg-item[title^="Die große Welle vor Kanagawa"]');
  ok(await wave.count() === 1 && /Hokusai/.test(await wave.getAttribute('title')), 'Tooltip mit Titel und Künstler');
  ok(await until(() => p.evaluate(() => { const i = document.querySelector('.cg-item[title^="Die große Welle"] img'); return i && i.complete && i.naturalWidth > 0 && i.naturalWidth <= 800; }), 8000),
     'Vorschaubild geladen (verkleinert)');
  await wave.click();
  ok(await until(async () => (await coverShown()) === '/covers/hokusai-welle.jpg'), 'Titelbild nach Auswahl sichtbar');
  const n1 = await api('GET', '/api/notes/' + id);
  ok(n1.cover === '/covers/hokusai-welle.jpg' && n1.cover_pos === 45, 'gespeichert mit empfohlener Position (' + n1.cover + ', ' + n1.cover_pos + ')');
  ok(await p.evaluate(() => /45%/.test(document.getElementById('pg-cover-img').style.objectPosition)), 'Position angewendet');

  console.log('Über dieses Bild, Zufällig');
  await p.hover('#pg-cover'); await p.evaluate(() => document.getElementById('pg-cover-change').click()); await wait(250);
  await p.locator('.menu .menu-item:has(.mi-t:text-is("Über dieses Bild"))').click();
  ok(await until(() => p.evaluate(() => !document.getElementById('pg-modal').hidden && /Katsushika Hokusai/.test(document.getElementById('pg-modal-body').innerText)
     && !!document.querySelector('#pg-modal-body a[href*="metmuseum.org"]'))), 'Nachweis mit Künstler und Link zur Met');
  await p.keyboard.press('Escape'); await p.evaluate(() => { const x = document.getElementById('pg-modal-close'); if (!document.getElementById('pg-modal').hidden && x) x.click(); });
  await p.evaluate(() => document.getElementById('pg-cover-change').click()); await wait(250);
  await p.locator('.menu .menu-item:has(.mi-t:text-is("Zufällig"))').click();
  ok(await until(async () => { const s = await coverShown(); return /^\/covers\/[a-z0-9-]+\.jpg$/.test(s) && s !== '/covers/hokusai-welle.jpg'; }), 'Zufällig: anderes Galerie-Bild');

  console.log('Einstellung');
  await p.evaluate(() => window.HKSettings.open()); await wait(800);
  const sw = await until(() => p.evaluate(() => !!document.getElementById('hk-random-cover')), 6000);
  ok(sw && await p.evaluate(() => document.getElementById('hk-random-cover').checked), 'Einstellungen → Seiten: Schalter „Neue Seiten mit zufälligem Titelbild“ an');
  if (sw) {
    await p.evaluate(() => document.getElementById('hk-random-cover').click());
    ok(await until(async () => (await api('GET', '/api/account/pages')).random_cover === false), 'Schalter aus -> gespeichert');
    await p.evaluate(() => document.getElementById('hk-random-cover').click());
    ok(await until(async () => (await api('GET', '/api/account/pages')).random_cover === true), 'wieder an');
  }
  ok(!errs.length, 'keine Skriptfehler (' + errs.slice(0, 3).join(' | ') + ')');
  await api('DELETE', '/api/notes/' + id); await api('DELETE', '/api/notes/' + r1.id);

  if (ADMIN && ACK) {
    console.log('Admin → Titelbilder');
    const a = await b.newContext({ viewport: { width: 1300, height: 900 }, locale: 'de-DE' });
    const u = new URL(ADMIN);
    await a.addCookies([{ name: 'hk_admin', value: ACK, domain: u.hostname, path: '/' }]);
    const q = await a.newPage(); const csp = [];
    q.on('console', m => { if (/Content Security Policy|Refused/.test(m.text())) csp.push(m.text()); });
    q.on('pageerror', e => csp.push(String(e)));
    await q.goto(ADMIN + '/covers');
    ok(await q.evaluate(() => document.querySelectorAll('form.adm-cov').length >= 22), 'Liste mit allen Bildern');
    ok(await q.evaluate(() => !!document.querySelector('.adm-nav a.on[href="/covers"]')), 'Reiter „Titelbilder“ aktiv');
    await q.evaluate(() => document.getElementById('cov-img-hokusai-welle').scrollIntoView());      // loading=lazy
    ok(await until(() => q.evaluate(() => { const i = document.getElementById('cov-img-hokusai-welle'); return i && i.complete && i.naturalWidth > 0; }), 8000), 'Vorschaubild geladen');
    if (process.env.SHOTS) { await q.evaluate(() => scrollTo(0, 0)); await q.screenshot({ path: process.env.SHOTS + '/admin.png', fullPage: false }); }
    await q.evaluate(() => { const r = document.querySelector('#c-hokusai-welle input[type=range]'); r.value = '10'; r.dispatchEvent(new Event('input')); });
    ok(await q.evaluate(() => /10%/.test(document.getElementById('cov-img-hokusai-welle').style.objectPosition)), 'Schieberegler verschiebt die Vorschau');
    ok(!csp.length, 'keine CSP-/Skriptfehler (' + csp.slice(0, 2).join(' | ') + ')');
    await a.close();
  }
  await b.close();
  console.log(`\nBrowser Titelbild-Galerie ${n - bad.length}/${n}` + (bad.length ? ' - FEHLER' : ' - ALLES GRUEN'));
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
