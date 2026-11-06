// Design-Runde 04 (UX-15, AE-17): leere Seite als Einladung (static/pages.js usShow, Stil in static/pm-editor.css).
// Nur Tiptap. Konto mit Tiptap-Editor (Admin editor.engine = tiptap, oder choice + Kontowahl Tiptap):
//   SAGA_BASE=http://host:port SAGA_USER=konto SAGA_PASS=... [SHOTS=/pfad] [PLAYWRIGHT=playwright] node tests/browser_leere_seite.js
// Geprueft: neue Seite ("+ Neue Seite" -> "Leere Seite") zeigt die Liste unter der ersten Zeile; erstes Zeichen blendet
// sie aus (ohne Layoutsprung), Loeschen zeigt sie wieder; Pfeil runter markiert (Goldbalken, aria-selected), Enter auf
// "Vorlage" oeffnet die Galerie, das Dokument bleibt unveraendert; Esc blendet aus; "Datenbank" bettet eine neue
// Datenbank ein; Datenbank-Seite und Nur-Lesen zeigen nichts; keine Seitenfehler.
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const path = require('path'), fs = require('fs');
const E = process.env, BASE = E.SAGA_BASE || E.HK_TEST_BASE, SHOTS = E.SHOTS || '';
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c || i === undefined ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 6000, step = 100) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await sleep(step); } return v; }

(async () => {
  const br = await PW.chromium.launch(E.PW_CHANNEL ? { channel: E.PW_CHANNEL } : {});
  const errs = [];
  async function session(opts) {
    const ctx = await br.newContext(Object.assign({ viewport: { width: 1280, height: 860 }, locale: 'de-DE' }, opts || {}));
    const p = await ctx.newPage();
    p.on('pageerror', (e) => errs.push(e.message));
    await p.goto(BASE + '/login');
    await p.fill('input[name=username], input#username, input[type=text]', E.SAGA_USER);
    await p.fill('input[type=password]', E.SAGA_PASS);
    await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
    await p.goto(BASE + '/notes'); await p.waitForSelector('#pg-new', { state: 'attached' }); await sleep(800);
    return p;
  }
  const shot = async (p, n) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, n + '.png') }); } };
  const vis = (p) => p.evaluate(() => { const b = document.querySelector('#pg-editor .ux-start'); return !!b && b.getBoundingClientRect().height > 0; });
  const p = await session({ colorScheme: 'dark' });
  ok('Tiptap-Editor aktiv (body[data-editor=tiptap])', await p.evaluate(() => document.body.dataset.editor === 'tiptap'));

  // 1. neue Seite ueber "+ Neue Seite" -> "Leere Seite"
  await p.click('#pg-new'); await sleep(300);
  await p.click('.menu:not([hidden]) .menu-item:has-text("Leere Seite")');
  await until(() => p.evaluate(() => !!document.querySelector('#pg-editor .ProseMirror')));
  ok('Neue leere Seite: Liste sichtbar', await until(() => vis(p)));
  const info = await p.evaluate(() => ({ head: document.querySelector('.ux-start-h').textContent,
    items: [...document.querySelectorAll('.ux-start-i')].map((b) => b.dataset.k), role: document.querySelector('.ux-start-l').getAttribute('role'),
    svg: [...document.querySelectorAll('.ux-start-i svg')].every((s) => getComputedStyle(s).strokeWidth === '1.7px' && s.getBoundingClientRect().width === 16) }));
  ok('Kopf "Einfach losschreiben – oder starten mit:", Eintraege Vorlage/Datenbank/Import, Linien-Symbole 16 px', /Einfach losschreiben/.test(info.head)
     && ['gal', 'db', 'imp'].every((k) => info.items.includes(k)) && info.role === 'listbox' && info.svg, info);
  await shot(p, 'leer-dunkel-desktop');

  // 2. Tippen blendet aus (ohne Layoutsprung), Loeschen zeigt wieder
  await p.click('#pg-editor .ProseMirror'); await sleep(150);
  const y0 = await p.evaluate(() => { const r = document.querySelector('#pg-editor').getBoundingClientRect(); return [r.top, r.height]; });
  await p.keyboard.type('H'); await sleep(250);
  ok('Erstes Zeichen: Liste weg', !(await vis(p)));
  const y1 = await p.evaluate(() => { const r = document.querySelector('#pg-editor').getBoundingClientRect(); return [r.top, r.height]; });
  ok('Kein Layoutsprung beim Tippen (Editor gleich hoch und gleich weit oben)', y0[0] === y1[0] && y0[1] === y1[1], { y0, y1 });
  await p.keyboard.press('Backspace'); await sleep(250);
  ok('Wieder leer: Liste wieder da', await until(() => vis(p), 2000));

  // 3. Tastatur: Pfeil runter markiert, Enter auf "Vorlage" oeffnet die Galerie, Dokument unveraendert
  await p.keyboard.press('ArrowDown'); await sleep(100);
  const mk = await p.evaluate(() => { const b = document.querySelector('.ux-start-i.on'); return b && { k: b.dataset.k, sel: b.getAttribute('aria-selected'), sh: getComputedStyle(b).boxShadow }; });
  ok('Pfeil runter: erster Eintrag markiert (aria-selected, Goldbalken)', mk && mk.k === 'gal' && mk.sel === 'true' && /inset/.test(mk.sh), mk);
  await shot(p, 'leer-dunkel-markiert');
  await p.keyboard.press('ArrowUp'); await sleep(80);
  ok('Pfeil hoch vom ersten: keine Markierung (normal tippen)', await p.evaluate(() => !document.querySelector('.ux-start-i.on')));
  await p.keyboard.press('ArrowDown'); await sleep(80);
  await p.keyboard.press('Enter');
  ok('Enter auf "Vorlage aus der Galerie": Galerie offen', await until(() => p.evaluate(() => !!document.querySelector('.hk-dialog-gal') && !document.getElementById('pg-modal').hidden), 4000));
  const docEmpty = await p.evaluate(() => { const d = window.HKPages && document.querySelector('#pg-editor .ProseMirror'); return d && d.textContent === '' && d.children.length === 1; });
  ok('Dokument dabei unveraendert leer (kein Absatz durch Enter)', docEmpty);
  await p.keyboard.press('Escape'); await sleep(300);
  if (await p.evaluate(() => !document.getElementById('pg-modal').hidden)) { await p.click('#pg-modal .hk-dialog-x, #pg-modal [aria-label="Schließen"]').catch(() => {}); await sleep(300); }

  // 4. Esc blendet aus; naechste leere Seite zeigt sie wieder
  await p.click('#pg-editor .ProseMirror'); await sleep(100);
  ok('Liste nach der Galerie wieder da (Seite weiter leer)', await vis(p));
  await p.keyboard.press('Escape'); await sleep(200);
  ok('Esc blendet aus', !(await vis(p)));
  await p.keyboard.type('a'); await p.keyboard.press('Backspace'); await sleep(250);
  ok('... und nach Tippen und Loeschen wieder da', await vis(p));

  // 5. Datenbank: neue Datenbank als Inhalt der Seite
  const pid = await p.evaluate(() => +location.hash.slice(1));
  await p.click('.ux-start-i[data-k="db"]');
  ok('"Datenbank": eingebettete Datenbank in der Seite, Liste weg', await until(() => p.evaluate(() => !!document.querySelector('#pg-editor .embed-db .embed-box') && !document.querySelector('.ux-start')), 8000));
  await sleep(1500);
  const kids = await p.evaluate((pid) => HK.api('GET', '/api/notes').then((l) => (l.items || l).filter((x) => x.parent_id === pid && x.kind === 'database').length), pid);
  ok('... Datenbank liegt unter der Seite', kids === 1, kids);

  // 6. Datenbank-Seite: nichts
  const dbId = (await p.evaluate(() => HK.api('POST', '/api/notes', { kind: 'database', title: 'Leer-DB' }))).id;
  await p.goto(BASE + '/notes#' + dbId); await sleep(1500);
  ok('Datenbank-Seite: keine Liste', !(await vis(p)));
  // 7. Seite mit Inhalt: nichts
  const full = (await p.evaluate(() => HK.api('POST', '/api/notes', { title: 'Voll' }))).id;
  await p.evaluate((id) => HK.api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks: [{ id: 'a1', type: 'paragraph', data: { text: 'Hallo' } }] }), format: 'blocks' }), full);
  await p.goto(BASE + '/notes#' + full); await sleep(1500);
  ok('Seite mit Inhalt: keine Liste', !(await vis(p)));

  // 8. Handy 390, hell
  const m = await session({ viewport: { width: 390, height: 844 }, colorScheme: 'light', isMobile: true, hasTouch: true });
  const nid = (await m.evaluate(() => HK.api('POST', '/api/notes', { title: '' }))).id;
  await m.goto(BASE + '/notes#' + nid); await sleep(1800);
  ok('Handy 390: Liste sichtbar und innerhalb des Bildschirms', await m.evaluate(() => { const b = document.querySelector('.ux-start'); if (!b) return false; const r = b.getBoundingClientRect(); return r.width > 0 && r.right <= innerWidth; }));
  await shot(m, 'leer-hell-handy');
  ok('Keine Seitenfehler (pageerror)', errs.length === 0, errs);
  await br.close();
  const bad = res.filter((x) => !x).length;
  console.log('\nLEERE-SEITE ' + (res.length - bad) + '/' + res.length + (bad ? ' - FEHLER' : ' - ALLES GRUEN'));
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
