// Unterseiten (2.5, Widget-Art subpages) im Web - Tiptap und Editor.js (SAGA_EDITOR=tiptap|editorjs).
// - "/Unterseiten" + Enter fuegt den Block ein (Editor.js: Einrichten geht auf, "Fertig" schliesst), "/Galerie" findet ihn
//   ueber die Suchbegriffe; gespeichert als widget/subpages
// - Liste: Unterseiten als Links in Baum-Reihenfolge, Symbol + Titel; Einrichten per Zahnrad (Tastatur): Ansicht Galerie,
//   Kartenbild (Titelbild/Inhalt/keins), Groesse; Galerie mit Bildern bzw. Platzhalter-Symbol, Hochformat bei "Inhalt"
// - Live: neue Unterseite (anderswo angelegt) erscheint ohne Neuladen
// - Klick auf eine Karte oeffnet die Seite; Handy (390): zwei Spalten; leer: "Unterseite anlegen" legt eine an
// Lokal: HK_TEST_BASE=http://127.0.0.1:8510 CK=<Cookie hk-test-a> CK2=<Cookie hk-test-b> SAGA_EDITOR=tiptap node tests/browser_unterseiten.js
// (CK2: zweite Person - legt die Unterseite fuer den Live-Teil an; Ereignisse der eigenen Sitzung kommen dort nicht an)
// (Admin editor.engine = choice, damit der Test den Editor des Kontos umstellen kann)
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(150); } }
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'de-DE' });
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
  const tag = engine === 'tiptap' || D.EDITOR === 'tiptap' ? 'Tiptap' : 'Editor.js';
  const made = [];
  const covers = ((await api('GET', '/api/covers')).covers || []).map((c) => c.url);
  const root = (await api('POST', '/api/notes', { title: 'UT Sammlung', cover: null })).id; made.push(root);
  await api('PATCH', '/api/notes/' + root, { format: 'blocks', body: JSON.stringify({ blocks: [{ id: 'p1', type: 'paragraph', data: { text: 'Meine Sammlung' } }] }) });
  const kids = [];
  for (const [i, t] of ['Erste Reise', 'Zweite Reise', 'Dritte Reise'].entries()) {
    kids.push((await api('POST', '/api/notes', { title: t, parent_id: root, icon: ['🌲', '⛵', ''][i], cover: i < 2 && covers[i] ? covers[i] : null })).id);
  }
  const SP = '.wg[data-kind="subpages"]';
  const open = async (id) => {
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
    await p.waitForSelector(D.anyText, { timeout: 15000 }).catch(() => {});
    await sleep(600);
  };

  // --- Slash-Befehl ---------------------------------------------------------------------------------------------------
  await open(root);
  await p.click(D.text('p1')); await p.keyboard.press('End'); await p.keyboard.press('Enter');
  await p.keyboard.type('/Galerie', { delay: 25 }); await sleep(700);
  const seen = await p.evaluate((sel) => [...document.querySelectorAll(sel)].filter((e) => e.getClientRects().length).map((e) => e.textContent.trim()), D.UI_ITEMS);
  ok(`${tag}: "/Galerie" findet "Unterseiten" ueber die Suchbegriffe`, seen.includes('Unterseiten'), seen);
  for (let i = 0; i < 8; i++) await p.keyboard.press('Backspace');
  await sleep(200);
  await p.keyboard.type('/Unterseiten', { delay: 25 }); await sleep(600);
  await p.keyboard.press('Enter');
  const ins = await until(() => p.locator(SP).count(), 8000);
  ok(`${tag}: "/Unterseiten" + Enter fuegt den Block ein`, ins === 1, ins);
  const done = p.locator('.wg-panel .wg-done');
  const auto = await until(() => done.count(), 4000);              // eben eingefuegt: Einrichten geht von selbst auf
  ok(`${tag}: Einrichten geht nach dem Einfuegen auf`, auto === 1, auto);
  if (auto) { await done.first().click(); await sleep(300); }
  const list = await until(async () => { const n = await p.locator(SP + ' .sp-list .sp-it').count(); return n === 3 ? n : 0; }, 8000);
  ok(`${tag}: Liste mit den drei Unterseiten`, list === 3, await p.locator(SP).first().innerHTML().catch(() => ''));
  const rows = await p.evaluate((sel) => [...document.querySelectorAll(sel + ' .sp-it')].map((a) => ({ t: a.textContent.trim(), href: a.getAttribute('href'), al: a.getAttribute('aria-label') })), SP);
  ok(`${tag}: Reihenfolge wie im Baum, Links mit aria-label, Symbol + Titel`, rows.map((r) => r.href).join() === kids.map((k) => '/notes#' + k).join()
     && rows[0].t === '🌲Erste Reise' && rows[0].al === 'Erste Reise' && rows[2].t.indexOf('Dritte Reise') > 0, rows);
  const saved = await until(async () => { const d = await api('GET', '/api/notes/' + root); return /"kind":\s*"subpages"/.test(d.body || '') ? d : null; }, 8000);
  ok(`${tag}: gespeichert als widget/subpages`, !!saved, saved && saved.body);

  // --- Einstellungen: Galerie ------------------------------------------------------------------------------------------
  await p.hover(SP); await p.locator(SP + ' .sp-cfg').focus(); await p.keyboard.press('Enter'); await sleep(300);
  const panel = p.locator('.wg-panel');
  ok(`${tag}: Zahnrad oeffnet das Einrichten (Tastatur)`, await panel.count() === 1);
  const selOf = (label) => panel.locator('label.wg-row', { has: p.locator('.wg-l', { hasText: label }) }).locator('select');
  await selOf('Ansicht').selectOption('gallery'); await sleep(200);
  const hasImg = await selOf('Kartenbild').count();
  const farbe = await panel.locator('.wg-l', { hasText: /^Farbe$/ }).count();
  ok(`${tag}: Galerie zeigt Kartenbild-Auswahl, Kartengroesse, keine Farbe`, hasImg === 1 && await panel.locator('.wg-l', { hasText: 'Kartengröße' }).count() === 1 && farbe === 0);
  await selOf('Kartenbild').selectOption('cover'); await sleep(300);
  await panel.locator('.wg-done').click(); await sleep(400);
  const gal = await until(() => p.evaluate((sel) => {
    const g = document.querySelector(sel + ' .sp-grid'); if (!g) return null;
    const pv = [...g.querySelectorAll('.sp-pv')].map((x) => { const r = x.getBoundingClientRect(); return { img: !!x.querySelector('img'), ph: x.classList.contains('sp-pv-ph'), ar: +(r.width / r.height).toFixed(2) }; });
    const imgs = [...g.querySelectorAll('img')]; return { pv: pv, loaded: imgs.length && imgs.every((i) => i.complete && i.naturalWidth > 0), cols: getComputedStyle(g).gridTemplateColumns.split(' ').length };
  }, SP), 8000);
  ok(`${tag}: Galerie mit Titelbildern (16:9) und Platzhalter`, gal && gal.pv.length === 3 && gal.pv.filter((x) => x.img).length === Math.min(2, covers.length)
     && gal.pv[2].ph && gal.pv.every((x) => Math.abs(x.ar - 16 / 9) < 0.08) && (covers.length === 0 || gal.loaded), gal);
  ok(`${tag}: Desktop mehrere Spalten`, gal && gal.cols >= 3, gal);
  const tc = await p.evaluate((sel) => { const t = document.querySelector(sel + ' .sp-grid .sp-t'); const s = getComputedStyle(t); return { c: s.color, fg: getComputedStyle(document.body).color, clamp: s.webkitLineClamp }; }, SP);
  ok(`${tag}: Titel in Textfarbe, hoechstens zwei Zeilen`, tc.c === tc.fg && String(tc.clamp) === '2', tc);
  await p.locator(SP + ' .sp-cfg').click(); await sleep(250);
  await selOf('Kartenbild').selectOption('content'); await sleep(300);
  await panel.locator('.wg-done').click(); await sleep(500);
  const ar = await until(() => p.evaluate((sel) => { const x = document.querySelector(sel + ' .sp-i-content .sp-pv'); if (!x) return 0; const r = x.getBoundingClientRect(); return +(r.width / r.height).toFixed(2); }, SP), 6000);
  ok(`${tag}: Kartenbild "Inhalt" im Hochformat 2:3 (ohne Bild: Platzhalter)`, Math.abs(ar - 2 / 3) < 0.05, ar);

  // --- Live ------------------------------------------------------------------------------------------------------------
  if (CK2) {                                             // andere Person mit "Bearbeiten" legt eine Unterseite an
    await api('PUT', '/api/notes/' + root + '/shares', { type: 'user', target: 'hk-test-b', role: 'edit' });
    const ctx2 = await br.newContext({ locale: 'de-DE' });
    await ctx2.addCookies([{ name: 'saga', value: CK2, domain: new URL(BASE).hostname, path: '/' }]);
    const p2 = await ctx2.newPage(); await p2.goto(BASE + '/notes');
    await p2.evaluate(async (root) => fetch('/api/notes', { method: 'POST', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Vierte Reise', parent_id: root, cover: null }) }).then((r) => r.status), root);
    const live = await until(async () => (await p.locator(SP + ' .sp-it').count()) === 4, 12000);
    ok(`${tag}: neue Unterseite (von anderer Person) erscheint ohne Neuladen`, live, await p.locator(SP + ' .sp-it').count());
    await ctx2.close();
  } else console.log('-- Live-Teil uebersprungen (CK2 fehlt)');

  // --- Klick oeffnet die Seite -----------------------------------------------------------------------------------------
  await sleep(500);
  await p.locator(SP + ` .sp-it[data-id="${kids[1]}"]`).click();
  const opened = await until(() => p.evaluate((id) => window.HKPages && HKPages.k && HKPages.k.cur ? HKPages.k.cur().id === id : location.hash === '#' + id, kids[1]), 8000);
  ok(`${tag}: Klick auf die Karte oeffnet die Seite`, !!opened && p.url().endsWith('#' + kids[1]), p.url());

  // --- Handy -----------------------------------------------------------------------------------------------------------
  await p.setViewportSize({ width: 390, height: 844 });
  await open(root);
  const mob = await until(() => p.evaluate((sel) => { const g = document.querySelector(sel + ' .sp-grid'); if (!g) return null; const w = document.querySelector(sel).getBoundingClientRect();
    return { cols: getComputedStyle(g).gridTemplateColumns.split(' ').length, over: [...g.querySelectorAll('.sp-it')].some((a) => a.getBoundingClientRect().right > w.right + 1) }; }, SP), 8000);
  ok(`${tag}: Handy - zwei Spalten, nichts laeuft ueber`, mob && mob.cols === 2 && !mob.over, mob);
  await p.setViewportSize({ width: 1440, height: 1000 });

  // --- Leer: Unterseite anlegen ----------------------------------------------------------------------------------------
  const leer = (await api('POST', '/api/notes', { title: 'UT Leer', cover: null })).id; made.push(leer);
  await api('PATCH', '/api/notes/' + leer, { format: 'blocks', body: JSON.stringify({ blocks: [{ id: 'sp', type: 'widget', data: { v: 1, kind: 'subpages', cfg: { view: 'gallery', image: 'cover' } } }] }) });
  await open(leer);
  const nb = p.locator(SP + ' .sp-new');
  ok(`${tag}: leer - freundlicher Hinweis und "Unterseite anlegen"`, await until(() => nb.count(), 6000) === 1
     && /Noch keine Unterseiten/.test(await p.locator(SP).textContent()));
  await nb.click();
  const child = await until(async () => { const t = await api('GET', '/api/notes'); const c = (Array.isArray(t) ? t : []).find((x) => x.parent_id === leer); return c && p.url().endsWith('#' + c.id) ? c : null; }, 8000);
  ok(`${tag}: "Unterseite anlegen" legt eine Unterseite an und oeffnet sie`, !!child && p.url().endsWith('#' + child.id), [p.url(), child, leer]);

  for (const id of made) await api('DELETE', '/api/notes/' + id);
  ok('Keine Seitenfehler', !errs.length, errs);
  await br.close();
  console.log('UNTERSEITEN-BROWSER (' + tag + ') ' + res.filter(Boolean).length + '/' + res.length + (res.every(Boolean) ? ' ALLES GRUEN' : ' FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
