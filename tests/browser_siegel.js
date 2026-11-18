const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
// Saga-Siegel (2.5, Bugs-DB 2156): Symbol-Menue -> Bereich "Saga-Siegel" (Farbwahl als Radiogruppe, Suche, Raster der
// 36 Motive mit Namen als aria-label/Tooltip, Pfeiltasten), Siegel setzen -> gespeichert und sichtbar im Seitenkopf,
// Seitenbaum, Unterseiten-Block, Datenbank-Zeile, Startseiten-Karten; Breadcrumbs/Titel ohne Pfad-Text (wie eigene Bilder);
// zuletzt gewaehlte Farbe gemerkt; "Icon entfernen". Hell und dunkel; keine Skriptfehler, keine CSP-Meldungen.
//   SAGA_BASE=… SAGA_USER=konto SAGA_PASS=… [SHOTS=<ordner> SHOT_TAG=nachher] node tests/browser_siegel.js
//   VORHER=1: nur Bilder vom Stand ohne Siegel (Menue hell/dunkel, Seite) - keine Pruefungen.
const BASE = process.env.SAGA_BASE || process.env.HK_TEST_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
const SHOTS = process.env.SHOTS, TAG = process.env.SHOT_TAG || (process.env.VORHER ? 'vorher' : 'nachher');
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const bad = []; let n = 0;
const ok = (c, m) => { n++; if (!c) { bad.push(m); console.log('  FEHLT: ' + m); } else console.log('  ok: ' + m); };
const wait = ms => new Promise(r => setTimeout(r, ms));
async function until(f, ms) { const t0 = Date.now(); while (Date.now() - t0 < (ms || 5000)) { if (await f()) return true; await wait(100); } return false; }
(async () => {
  const b = await chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await b.newContext({ viewport: { width: 1400, height: 900 }, locale: 'de-DE' });
  const p = await ctx.newPage(); const errs = [], csp = [];
  p.on('pageerror', e => errs.push(String(e)));
  p.on('console', m => { if (/Content Security Policy|Refused to/.test(m.text())) csp.push(m.text()); });
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  const api = (m, url, body) => p.evaluate(async ([m, url, body]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const t = await r.text(); try { return JSON.parse(t); } catch (e) { return t; }
  }, [m, url, body]);
  const open = async id => {
    await p.goto(BASE + '/notes#' + id);
    await until(() => p.evaluate(i => !document.getElementById('page').hidden && window.location.hash === '#' + i, id));
    await wait(700);
  };
  const theme = async t => { await p.evaluate(t => window.SagaTheme.set(t), t); await wait(300); };
  const shot = async (name, loc) => { if (!SHOTS) return; await wait(400); await (loc || p).screenshot({ path: `${SHOTS}/${TAG}-${name}.png` }); };
  const menuShot = async name => {
    if (!SHOTS) return;
    const r = await p.evaluate(() => { const m = document.querySelector('body > .menu:not(.menu-sub)'); if (!m || m.hidden) return null; const x = m.getBoundingClientRect();
      const a = document.getElementById('pg-icon').getBoundingClientRect(); const l = Math.min(x.left, a.left) - 12, t = Math.min(x.top, a.top) - 12;
      return { x: Math.max(0, l), y: Math.max(0, t), width: Math.max(x.right, a.right) - Math.max(0, l) + 12, height: Math.max(x.bottom, a.bottom) - Math.max(0, t) + 12 }; });
    if (r) { await wait(500); await p.screenshot({ path: `${SHOTS}/${TAG}-${name}.png`, clip: r }); }
  };
  const openMenu = async () => {
    await p.evaluate(() => { const m = document.querySelector('body > .menu:not(.menu-sub)'); if (m && !m.hidden) document.getElementById('pg-icon').click(); });
    await p.evaluate(() => document.getElementById('pg-icon').click());
    return until(() => p.evaluate(() => { const m = document.querySelector('body > .menu:not(.menu-sub)'); return m && !m.hidden && !!m.querySelector('.emoji-grid'); }));
  };
  const sfx = String(Date.now()).slice(-5);
  const P = (await api('POST', '/api/notes', { title: 'Siegel-Probe ' + sfx, cover: null })).id;
  const K = (await api('POST', '/api/notes', { title: 'Kind ' + sfx, parent_id: P, cover: null })).id;

  if (process.env.VORHER) {
    await open(P); await theme('light');
    await openMenu(); await menuShot('menue-hell');
    await theme('dark'); await openMenu(); await menuShot('menue-dunkel');
    await p.keyboard.press('Escape'); await theme('light');
    await api('PATCH', '/api/notes/' + P, { icon: '🦉' }); await open(P); await shot('seite');
    await api('DELETE', '/api/notes/' + K); await api('DELETE', '/api/notes/' + P);
    await b.close(); console.log('vorher: Bilder geschrieben'); return;
  }

  console.log('Menue');
  await open(P); await theme('light');
  ok(await openMenu(), 'Symbol-Menue offen');
  ok(await until(() => p.evaluate(() => document.querySelectorAll('.menu .sgl-grid .sgl-m').length === 36)), 'Bereich „Saga-Siegel“ mit 36 Motiven');
  ok(await p.evaluate(() => [...document.querySelectorAll('.menu .menu-label')].some(x => x.textContent === 'Saga-Siegel')), 'Überschrift „Saga-Siegel“ unter den Emoji');
  ok(await p.evaluate(() => { const g = document.querySelector('.menu .emoji-grid'), s = document.querySelector('.menu .sgl-box');
    return g && s && (g.compareDocumentPosition(s) & Node.DOCUMENT_POSITION_FOLLOWING); }), 'Siegel stehen nach den Emoji');
  const radios = await p.evaluate(() => [...document.querySelectorAll('.menu .sgl-colors [role=radio]')].map(x => x.textContent + ':' + x.getAttribute('aria-checked')));
  ok(radios.join(',') === 'Weinrot:true,Smaragd:false,Elfenbein:false', 'Farbwahl als Radiogruppe, Weinrot vorgewählt (' + radios.join(', ') + ')');
  ok(await p.evaluate(() => document.querySelector('.menu .sgl-colors').getAttribute('role') === 'radiogroup' && !!document.querySelector('.menu .sgl-colors').getAttribute('aria-label')),
     'Radiogruppe beschriftet');
  ok(await until(() => p.evaluate(() => [...document.querySelectorAll('.menu .sgl-m img')].every(i => i.complete && i.naturalWidth > 0)), 8000), 'alle Siegel-Bilder geladen');
  const lab = await p.evaluate(() => { const x = document.querySelector('.menu .sgl-m[data-m="rabe"]'); return [x.getAttribute('aria-label'), x.title, x.querySelector('img').getAttribute('src'), x.querySelector('img').alt]; });
  ok(lab[0] === 'Siegel Rabe (Weinrot)' && lab[1] === 'Rabe (Weinrot)' && lab[2] === '/siegel/weinrot/rabe.svg' && lab[3] === '', 'Name als aria-label und Tooltip (' + lab.join(' | ') + ')');
  ok(await p.evaluate(() => [...document.querySelectorAll('.menu .sgl-m')].filter(x => x.tabIndex === 0).length === 1), 'nur ein Motiv in der Tab-Reihenfolge');
  await menuShot('menue-hell');
  await theme('dark'); await openMenu(); await until(() => p.evaluate(() => document.querySelectorAll('.menu .sgl-m').length === 36));
  await until(() => p.evaluate(() => [...document.querySelectorAll('.menu .sgl-m img')].every(i => i.complete && i.naturalWidth > 0)), 8000);
  await menuShot('menue-dunkel');
  await theme('light'); await openMenu(); await until(() => p.evaluate(() => document.querySelectorAll('.menu .sgl-m').length === 36));

  console.log('Farbwahl, Tastatur, setzen');
  await p.locator('.menu .sgl-c:text-is("Smaragd")').click();
  ok(await p.evaluate(() => document.querySelector('.menu .sgl-c-smaragd').getAttribute('aria-checked') === 'true'
     && [...document.querySelectorAll('.menu .sgl-m img')].every(i => /^\/siegel\/smaragd\//.test(i.getAttribute('src')))), 'Smaragd gewählt: alle Motive in Smaragd');
  await p.keyboard.press('ArrowRight');
  ok(await p.evaluate(() => document.activeElement.classList.contains('sgl-c-elfenbein') && document.activeElement.getAttribute('aria-checked') === 'true'),
     'Pfeil rechts in der Radiogruppe -> Elfenbein');
  await p.keyboard.press('ArrowLeft');
  ok(await p.evaluate(() => document.activeElement.classList.contains('sgl-c-smaragd')), 'Pfeil links -> zurück auf Smaragd');
  await p.evaluate(() => document.querySelector('.menu .sgl-m[data-m="yggdrasil"]').focus());
  await p.keyboard.press('ArrowRight');
  ok(await p.evaluate(() => document.activeElement.dataset.m === 'rabe'), 'Pfeil rechts im Raster -> Rabe');
  await p.keyboard.press('ArrowDown');
  const cols = await p.evaluate(() => getComputedStyle(document.querySelector('.menu .sgl-grid')).gridTemplateColumns.split(' ').length);
  ok(await p.evaluate(c => document.activeElement === document.querySelectorAll('.menu .sgl-m')[1 + c], cols), 'Pfeil runter -> eine Zeile tiefer (' + cols + ' Spalten)');
  await p.keyboard.press('End');
  ok(await p.evaluate(() => document.activeElement.dataset.m === 'teetasse' && document.activeElement.tabIndex === 0), 'Ende -> letztes Motiv (Teetasse)');
  await p.keyboard.press('Home'); await p.keyboard.press('ArrowRight');
  await p.keyboard.press('Enter');
  ok(await until(async () => (await api('GET', '/api/notes/' + P)).icon === '/siegel/smaragd/rabe.svg'), 'Enter auf Rabe (Smaragd) -> gespeichert');
  ok(await p.evaluate(() => { const m = document.querySelector('body > .menu:not(.menu-sub)'); return !m || m.hidden; }), 'Menü geschlossen');
  ok(await until(() => p.evaluate(() => { const i = document.querySelector('#pg-icon img.page-icon-img'); return i && i.getAttribute('src') === '/siegel/smaragd/rabe.svg' && i.complete && i.naturalWidth > 0; })),
     'Seitenkopf zeigt das Siegel (Vektor, ohne ?w=)');
  ok(await until(() => p.evaluate(id => { const a = document.querySelector(`.pg-tree [data-id="${id}"] img.pg-ic, .pg-tree img.pg-ic[src="/siegel/smaragd/rabe.svg"]`);
    return !!a && a.complete && a.naturalWidth > 0; }, P)), 'Seitenbaum zeigt das Siegel');
  const txt = await p.evaluate(() => document.body.innerText + ' ' + document.title);
  ok(!/\/siegel\//.test(txt), 'kein Pfad als Text (Brotkrumen, Fenstertitel, Baum)');

  console.log('Erneut öffnen, Suche');
  ok(await openMenu() && await until(() => p.evaluate(() => document.querySelectorAll('.menu .sgl-m').length === 36)), 'Menü wieder offen');
  ok(await p.evaluate(() => { const x = document.querySelector('.menu .sgl-m[data-m="rabe"]'); return x.classList.contains('on') && x.getAttribute('aria-pressed') === 'true' && x.tabIndex === 0
    && document.querySelector('.menu .sgl-c-smaragd').getAttribute('aria-checked') === 'true'; }), 'aktuelles Siegel markiert, Farbe des Siegels gewählt');
  await p.locator('.menu .sgl-q').fill('kat');
  ok(await p.evaluate(() => [...document.querySelectorAll('.menu .sgl-m')].filter(x => !x.hidden).map(x => x.dataset.m).join() === 'katze'), 'Suche „kat“ -> nur Katze');
  await p.locator('.menu .sgl-q').fill('xyz');
  ok(await p.evaluate(() => !document.querySelector('.menu .sgl-none').hidden), 'Suche ohne Treffer -> Hinweis');
  await p.locator('.menu .sgl-q').fill('Stern'); await p.locator('.menu .sgl-q').press('Enter');
  ok(await until(async () => (await api('GET', '/api/notes/' + P)).icon === '/siegel/smaragd/stern.svg'), 'Suche „Stern“ + Enter -> gesetzt');

  console.log('Anzeige an weiteren Stellen');
  await api('PATCH', '/api/notes/' + K, { icon: '/siegel/weinrot/kerze.svg' });
  await p.goto(BASE + '/'); await wait(500);          // Seite P nicht offen, waehrend ihr Inhalt per API geaendert wird
  const pr = await api('PATCH', '/api/notes/' + P, { format: 'blocks', body: JSON.stringify({ blocks: [{ id: 'sp1', type: 'widget', data: { v: 1, kind: 'subpages', cfg: {} } }] }) });
  ok(pr && pr.ok !== false && !pr.error, 'Unterseiten-Block per API eingefuegt (' + JSON.stringify(pr).slice(0, 120) + ')');
  const D = (await api('POST', '/api/notes', { title: 'Siegel-DB ' + sfx, kind: 'database', icon: '/siegel/elfenbein/truhe.svg' })).id;
  const Z = (await api('POST', '/api/notes', { title: 'Zeile ' + sfx, parent_id: D, icon: '/siegel/weinrot/sanduhr.svg' })).id;
  await open(P);
  ok(await until(() => p.evaluate(() => { const i = document.querySelector('img.sp-ic[src="/siegel/weinrot/kerze.svg"], .sp-ic img[src="/siegel/weinrot/kerze.svg"]'); return i && i.complete && i.naturalWidth > 0; }), 8000),
     'Unterseiten-Block zeigt das Siegel der Unterseite');
  await p.evaluate(id => { const t = document.querySelector(`.pg-tree [data-id="${id}"]`); const tw = t && t.querySelector('.pg-tw, .tw, [aria-expanded="false"]'); if (tw) tw.click(); }, P);
  await shot('seite-baum-kopf');
  await theme('dark'); await shot('seite-baum-kopf-dunkel'); await theme('light');
  await open(D);
  ok(await until(() => p.evaluate(() => { const i = document.querySelector('#pg-icon img[src="/siegel/elfenbein/truhe.svg"]'); return i && i.complete && i.naturalWidth > 0; })), 'Datenbank-Kopf zeigt das Siegel');
  ok(await until(() => p.evaluate(() => { const i = document.querySelector('img.db-row-icon[src="/siegel/weinrot/sanduhr.svg"]'); return i && i.complete && i.naturalWidth > 0; }), 8000),
     'Datenbank-Zeile zeigt das Siegel');
  await shot('datenbank');
  await p.goto(BASE + '/'); await wait(1200);
  const card = await p.evaluate(() => !!document.querySelector('.hz-card-ic img[src^="/siegel/"]'));
  ok(card || !(await p.evaluate(() => !!document.getElementById('hz-cards'))), 'Startseite „Weitermachen“: Siegel als Bild (falls Karten da)');

  console.log('Farbe gemerkt, entfernen');
  await api('PATCH', '/api/notes/' + K, { icon: '' }); await open(K);
  ok(await openMenu() && await until(() => p.evaluate(() => document.querySelectorAll('.menu .sgl-m').length === 36)), 'Menü auf Seite ohne Siegel');
  ok(await p.evaluate(() => document.querySelector('.menu .sgl-c-smaragd').getAttribute('aria-checked') === 'true'), 'zuletzt gewählte Farbe (Smaragd) vorgewählt');
  await p.keyboard.press('Escape');
  await open(P); await openMenu();
  await p.locator('.menu .menu-item:has(.mi-t:text-is("Icon entfernen"))').click();
  ok(await until(async () => !(await api('GET', '/api/notes/' + P)).icon), 'Icon entfernen');
  ok(await p.evaluate(() => !document.querySelector('#pg-icon img')), 'Kopf ohne Bild');

  ok(!errs.length, 'keine Skriptfehler (' + errs.join(' | ').slice(0, 300) + ')');
  ok(!csp.length, 'keine CSP-Meldungen (' + csp.join(' | ').slice(0, 300) + ')');
  for (const id of [Z, D, K, P]) await api('DELETE', '/api/notes/' + id);
  await b.close();
  console.log(`\nBROWSER-SIEGEL ${n - bad.length}/${n} OK – ` + (bad.length ? 'FEHLER' : 'ALLES GRUEN'));
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
