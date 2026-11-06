const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
// Seitenmenue "···" (Design-Runde UX-18/19): Gruppen mit Trennern in fester Reihenfolge, "Link kopieren" per Menue und
// Kuerzel (Strg+L bzw. ⌘L, Kuerzel rechts im Menue), "Darstellung" als Untermenue mit sichtbarem Haken an der
// aktiven Schrift; "Teilen …"/"Veröffentlichen …" nur, wenn der Kopfzeilen-Knopf fehlt.
//   SAGA_BASE=http://127.0.0.1:8099 SAGA_USER=konto SAGA_PASS=… [PLAYWRIGHT=…] node tests/browser_seitenmenue.js
// Ein Konto mit Passwort; die Reihe legt ihre Seite selbst an und wirft sie am Ende in den Papierkorb.
const BASE = process.env.SAGA_BASE || process.env.HK_TEST_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const bad = []; let n = 0;
const ok = (c, m) => { n++; if (!c) { bad.push(m); console.log('  FEHLT: ' + m); } else console.log('  ok: ' + m); };
const wait = ms => new Promise(r => setTimeout(r, ms));
async function until(f, ms) { const t0 = Date.now(); while (Date.now() - t0 < (ms || 4000)) { if (await f()) return true; await wait(100); } return false; }
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1400, height: 900 }, locale: 'de-DE' });
  await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE });
  const p = await ctx.newPage(); const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  const api = (m, url, body) => p.evaluate(async ([m, url, body]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const t = await r.text(); try { return JSON.parse(t); } catch (e) { return t; }
  }, [m, url, body]);
  const id = (await api('POST', '/api/notes', { title: 'Menütest ' + String(Date.now()).slice(-5) })).id;
  await p.goto(BASE + '/notes#' + id);
  await until(() => p.evaluate(() => !document.getElementById('page').hidden && !!document.getElementById('pg-title').value));
  await wait(500);
  const struct = () => p.evaluate(() => [...document.querySelector('.menu:not(.menu-sub)').children].map(x =>
    x.classList.contains('menu-sep') ? '|' : x.classList.contains('menu-item') && !x.hidden ? (x.querySelector('.mi-t') || x).textContent.trim() : null).filter(Boolean));

  console.log('Aufbau');
  await p.click('#pg-more'); await wait(250);
  const s = await struct(); console.log('   ', s.join(' · '));
  const groups = s.join('\n').split('\n|\n').map(g => g.split('\n'));
  const has = (g, t) => groups[g] && groups[g].some(x => x.startsWith(t));
  ok(groups.length === 5, 'fuenf Gruppen mit Trennern (' + groups.length + ')');
  ok(has(0, 'Zu Favoriten') && groups[0].indexOf('Link kopieren') === 1 && has(0, 'Duplizieren') && has(0, 'Verschieben nach'), 'Gruppe 1: Favoriten, Link kopieren, Duplizieren, Verschieben');
  ok(groups[1] && groups[1].length === 1 && groups[1][0] === 'Darstellung', 'Gruppe 2: nur „Darstellung ›“');
  ok(has(2, 'Versionsverlauf') && has(2, 'Lokaler Graph'), 'Gruppe 3: Versionsverlauf, Lokaler Graph');
  ok(has(3, 'Importieren') && has(3, 'Exportieren') && has(3, 'Als Vorlage') && has(3, 'Als Startseite'), 'Gruppe 4: Import, Export, Vorlage, Startseite');
  ok(groups[4] && groups[4][0] === 'Archivieren' && groups[4][1] === 'In den Papierkorb', 'Gruppe 5: Archivieren, Papierkorb');
  ok(!s.some(x => /^(Teilen|Veröffentlichen)/.test(x)), 'Teilen/Veröffentlichen fehlen (Knopf „Teilen“ steht in der Kopfzeile)');
  ok(!s.some(x => /^(Volle Breite|Schrift|Kleiner Text|Titelbild|Seite sperren)/.test(x)), 'Darstellungs-Eintraege nicht mehr auf oberster Ebene');
  ok(s.filter(x => x !== '|').length <= 17, 'hoechstens 17 Eintraege (' + s.filter(x => x !== '|').length + ')');
  const li = await p.evaluate(() => { const it = [...document.querySelectorAll('.menu:not(.menu-sub) .menu-item')].find(x => x.querySelector('.mi-t').textContent === 'Link kopieren');
    return it && { kbd: (it.querySelector('kbd.mi-kbd') || {}).textContent || '', keys: it.getAttribute('aria-keyshortcuts'), svg: !!it.querySelector('.mi-ic svg') }; });
  ok(li && /Strg\+L|Ctrl\+L|⌘L/.test(li.kbd) && /^(Control|Meta)\+L$/.test(li.keys || '') && li.svg, 'Link kopieren: Kuerzel rechts, aria-keyshortcuts, Liniensymbol (' + JSON.stringify(li) + ')');

  console.log('Link kopieren');
  const clip = () => p.evaluate(() => navigator.clipboard.readText());
  await p.evaluate(() => navigator.clipboard.writeText('-'));
  await p.locator('.menu .menu-item:has(.mi-t:text-is("Link kopieren"))').click();
  ok(await until(async () => (await clip()) === BASE.replace(/\/$/, '') + '/notes#' + id), 'Menue: Link in der Zwischenablage');
  ok(await until(() => p.evaluate(() => /Link kopiert/.test(document.body.innerText))), 'Toast „Link kopiert“');
  await p.evaluate(() => navigator.clipboard.writeText('-'));
  await p.focus('#pg-title');
  await p.keyboard.press(process.platform === 'darwin' ? 'Meta+KeyL' : 'Control+KeyL');
  ok(await until(async () => (await clip()) === BASE.replace(/\/$/, '') + '/notes#' + id), 'Strg+L: Link in der Zwischenablage');
  await p.evaluate(() => navigator.clipboard.writeText('-'));
  await p.evaluate(() => document.activeElement.blur());
  await p.keyboard.press(process.platform === 'darwin' ? 'Meta+KeyL' : 'Control+KeyL');
  ok(await until(async () => (await clip()) === BASE.replace(/\/$/, '') + '/notes#' + id), 'Strg+L ausserhalb von Feldern (global auf der Seite)');

  console.log('Darstellung');
  const sub = () => p.evaluate(() => { const m = document.querySelector('.menu-sub:not([hidden])'); if (!m) return null;
    return [...m.children].map(x => x.classList.contains('menu-label') ? '#' + x.textContent.trim() : x.classList.contains('menu-sep') ? '|' :
      x.classList.contains('menu-item') ? (x.querySelector('.mi-t').textContent.trim() + (x.getAttribute('aria-checked') === 'true' ? '[x]' : '') + (x.querySelector('.mi-ic svg') && x.classList.contains('mi-radio') && x.getAttribute('aria-checked') === 'true' ? '✓' : '')) : null).filter(Boolean); });
  const openSub = async () => { await p.click('#pg-more'); await wait(200); await p.locator('.menu .menu-item:has(.mi-t:text-is("Darstellung"))').click(); await wait(250); return sub(); };
  let d = await openSub(); console.log('   ', JSON.stringify(d));
  ok(d && d.some(x => x.startsWith('Volle Breite')) && d.includes('#Schrift') && d.some(x => x.startsWith('Kleiner Text')) && d.some(x => /^Seite (ent)?sperren/.test(x)), 'Untermenue: Breite, Schrift, kleiner Text, Sperre');
  ok(d && d.includes('Standard[x]✓') && d.includes('Serif') && d.includes('Mono'), 'aktive Schrift mit sichtbarem Haken und aria-checked');
  await p.locator('.menu-sub .menu-item:has(.mi-t:text-is("Serif"))').click();
  ok(await until(async () => (await api('GET', '/api/notes/' + id)).font === 'serif'), 'Serif gespeichert');
  await wait(300);
  d = await openSub();
  ok(d && d.includes('Serif[x]✓') && d.includes('Standard'), 'Haken wandert zur gewaehlten Schrift');
  // Tastatur: Pfeil rechts oeffnet das Untermenue, Esc schliesst es wieder
  await p.keyboard.press('Escape'); await p.keyboard.press('Escape'); await wait(200);
  await p.focus('#pg-more'); await p.keyboard.press('Enter'); await wait(250);
  for (let i = 0; i < 20; i++) { if (await p.evaluate(() => (document.activeElement.querySelector('.mi-t') || {}).textContent === 'Darstellung')) break; await p.keyboard.press('ArrowDown'); }
  await p.keyboard.press('ArrowRight'); await wait(250);
  ok(await p.evaluate(() => !!document.activeElement.closest('.menu-sub')), 'Tastatur: → oeffnet „Darstellung“');
  await p.keyboard.press('Escape'); await p.keyboard.press('Escape');

  await api('DELETE', '/api/notes/' + id);
  ok(!errs.length, 'keine Seitenfehler: ' + errs.join(' | '));
  console.log(n - bad.length + '/' + n + (bad.length ? '\nFEHLER:\n- ' + bad.join('\n- ') : '\nALLES GRUEN'));
  await b.close();
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
