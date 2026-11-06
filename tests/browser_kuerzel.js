// Saga 2.5 (Design-Runde, UX-40/41) - Tastenkuerzel-Uebersicht und Kontomenue:
//   Strg/⌘+/ und "?" (ausserhalb von Eingaben) oeffnen den Dialog "Tastenkuerzel" (gruppiert), auch ueber das Kontomenue;
//   im Editor bleibt Strg/⌘+/ das Blockmenue. Mac zeigt ⌘ ⌥ ⇧, sonst Strg/Alt/Shift. Strg/⌘+Alt+N legt eine leere Seite an
//   (Heute -> /notes, auf /notes direkt). Kontomenue: Sprache als Untermenue, "Admin-Bereich ↗" nur fuer Admins.
//   SAGA_BASE=… SAGA_USER=… SAGA_PASS=… [SAGA_ADMIN_USER=… (Admin, Server mit ADMIN_URL)] node tests/browser_kuerzel.js
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const E = process.env, BASE = E.SAGA_BASE, USER = E.SAGA_USER, PASS = E.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
(async () => {
  const b = await chromium.launch({ channel: E.PW_CHANNEL || undefined }), res = [], errs = [];
  const ok = (c, m, x) => { res.push([!!c, m]); console.log((c ? 'OK   ' : 'FAIL ') + m + (c || x === undefined ? '' : ' ' + JSON.stringify(x))); };
  async function login(u, mac) {
    const ctx = await b.newContext({ viewport: { width: 1300, height: 860 }, locale: 'de-DE' });
    if (mac) await ctx.addInitScript(() => { Object.defineProperty(Navigator.prototype, 'platform', { get: () => 'MacIntel' }); });
    const p = await ctx.newPage(); p.on('pageerror', e => errs.push(String(e)));
    await p.goto(BASE + '/login'); await p.fill('input[name=username]', u); await p.fill('input[name=password]', PASS);
    await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
    return p;
  }
  const p = await login(USER, false);
  await require('./lib/editor_dom').useEditor(p);   // Editor-Modus ausdruecklich (SAGA_EDITOR=tiptap|editorjs)
  const api = (m, u, body) => p.evaluate(([m, u, body]) => fetch(u, { method: m, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Saga': '1' },
    body: body ? JSON.stringify(body) : undefined }).then(r => r.json()), [m, u, body]);
  const kz = () => p.evaluate(() => !!document.querySelector('.kz-modal'));
  const count = async () => { const l = await api('GET', '/api/notes'); return Array.isArray(l) ? l.length : -1; };

  // --- Uebersicht auf Heute -----------------------------------------------------------------------------------------
  await p.goto(BASE + '/dashboard'); await p.waitForTimeout(1200);
  await p.keyboard.press('Control+/'); await p.waitForTimeout(300);
  ok(await kz(), 'Strg+/ oeffnet "Tastenkürzel"');
  const info = await p.evaluate(() => {
    const d = document.querySelector('.kz-dialog');
    return { title: d.querySelector('h2').textContent, modal: d.getAttribute('aria-modal'), groups: [...d.querySelectorAll('.kz-group h3')].map(h => h.textContent),
      rows: [...d.querySelectorAll('.kz-row')].map(r => r.querySelector('dd').textContent + ' = ' + r.querySelector('dt').textContent),
      focusIn: d.contains(document.activeElement) };
  });
  ok(info.title === 'Tastenkürzel' && info.modal === 'true' && info.focusIn, 'Dialog mit Titel, aria-modal, Fokus drin', info);
  ok(['Allgemein', 'Navigation', 'Seiten und Editor', 'Chats', 'Marin'].every(g => info.groups.indexOf(g) >= 0), 'Gruppen Allgemein, Navigation, Seiten/Editor, Chats, Marin', info.groups);
  const row = t => info.rows.find(r => r.indexOf(t) === 0) || '';
  ok(/Strg\+K/.test(row('Schnellsuche')), 'PC: Schnellsuche = Strg+K', row('Schnellsuche'));
  ok(/Strg\+Alt\+N/.test(row('Neue leere Seite')), 'PC: Neue leere Seite = Strg+Alt+N');
  ok(/Strg\+L/.test(row('Link zur Seite kopieren')) && !/Alt/.test(row('Link zur Seite kopieren')), 'Link zur Seite kopieren = Strg+L');
  ok(/Strg\+J/.test(row('Marin öffnen')), 'Marin = Strg+J');
  ok(/Strg\+Shift\+J/.test(row('Vorschau: nächster')), 'Peek: Strg+Shift+J');
  ok(!info.rows.some(r => /⌘|⌥/.test(r)), 'PC: keine Mac-Zeichen');
  await p.keyboard.press('Tab'); await p.keyboard.press('Tab'); await p.keyboard.press('Tab');
  ok(await p.evaluate(() => document.querySelector('.kz-dialog').contains(document.activeElement)), 'Tab bleibt im Dialog');
  await p.keyboard.press('Escape'); await p.waitForTimeout(200);
  ok(!(await kz()), 'Esc schliesst');
  // "?" ausserhalb von Eingaben - im Suchfeld der Seitenleiste nicht
  await p.evaluate(() => document.activeElement && document.activeElement.blur());
  await p.keyboard.press('Shift+?'); await p.waitForTimeout(300);
  ok(await kz(), '"?" oeffnet die Uebersicht');
  await p.keyboard.press('Escape'); await p.waitForTimeout(150);
  await p.focus('#sh-search'); await p.keyboard.type('?'); await p.waitForTimeout(200);
  ok(!(await kz()) && await p.inputValue('#sh-search') === '?', '"?" im Eingabefeld ist nur ein Zeichen');
  await p.fill('#sh-search', ''); await p.evaluate(() => document.activeElement.blur());

  // --- Kontomenue: Tastenkuerzel, Sprache als Untermenue, kein Admin fuer normale Konten ------------------------------
  await p.click('#hk-user'); await p.waitForTimeout(250);
  const items = await p.$$eval('.menu:not(.menu-sub) .menu-item', l => l.map(x => x.textContent));
  ok(items.some(t => /^Tastenkürzel/.test(t)), 'Kontomenue: "Tastenkürzel"', items);
  ok(!items.some(t => /Admin-Bereich/.test(t)), 'Kontomenue: kein "Admin-Bereich" ohne Admin-Rechte');
  ok(!items.some(t => /Hell|Dunkel/.test(t)), 'Kontomenue: kein Hell/Dunkel-Schalter');
  await p.click('.menu-item:has-text("Sprache")'); await p.waitForTimeout(600);
  const langs = await p.$$eval('.menu-sub .menu-item', l => l.map(x => x.textContent + (x.getAttribute('aria-checked') === 'true' ? '*' : '')));
  ok(langs.some(t => /English/.test(t)) && langs.some(t => /Automatisch\*/.test(t)), 'Untermenue Sprache: Sprachen, aktuelle markiert', langs);
  await p.keyboard.press('Escape'); await p.keyboard.press('Escape'); await p.waitForTimeout(150);
  await p.click('#hk-user'); await p.waitForTimeout(200);
  await p.click('.menu-item:has-text("Tastenkürzel")'); await p.waitForTimeout(300);
  ok(await kz(), 'Kontomenue -> Tastenkürzel oeffnet die Uebersicht');
  await p.keyboard.press('Escape'); await p.waitForTimeout(200);
  ok(await p.evaluate(() => document.activeElement && document.activeElement.id) === 'hk-user', 'Fokus zurueck am Kontomenue-Knopf');

  // --- Strg+Alt+N -----------------------------------------------------------------------------------------------------
  const n0 = await count();
  await p.keyboard.press('Control+Alt+n');
  await p.waitForURL(/\/notes/, { timeout: 8000 }).catch(() => {}); await p.waitForTimeout(2000);
  const n1 = await count();
  ok(/\/notes#\d+$/.test(p.url()) && n1 === n0 + 1, 'Heute: Strg+Alt+N legt eine leere Seite an und oeffnet sie', { url: p.url(), n0, n1 });
  ok(!(await p.evaluate(() => location.search)), 'Adresse ohne ?new=blank');
  await p.evaluate(() => document.activeElement && document.activeElement.blur());
  await p.keyboard.press('Control+Alt+n'); await p.waitForTimeout(1800);
  ok(await count() === n1 + 1, '/notes: Strg+Alt+N legt direkt eine Seite an');

  // --- Im Editor bleibt Strg+/ das Blockmenue ---------------------------------------------------------------------
  await p.waitForTimeout(500);
  const ed = await p.$('#pg-editor [contenteditable="true"]');
  if (ed) {
    await ed.click(); await p.keyboard.type('Hallo'); await p.waitForTimeout(300);
    await p.keyboard.press('Control+/'); await p.waitForTimeout(400);
    ok(!(await kz()) && await p.evaluate(() => HK.menuOpen()), 'Editor: Strg+/ oeffnet das Blockmenue, nicht die Uebersicht');
    await p.keyboard.press('Escape');
  } else ok(false, 'Editor nicht gefunden');
  // /notes ausserhalb des Editors: Uebersicht; Befehl in der Schnellsuche
  await p.evaluate(() => document.activeElement && document.activeElement.blur()); await p.waitForTimeout(200);
  await p.keyboard.press('Control+k'); await p.waitForTimeout(300); await p.keyboard.type('>tasten'); await p.waitForTimeout(200);
  await p.keyboard.press('Enter'); await p.waitForTimeout(300);
  ok(await kz(), 'Schnellsuche-Befehl "Tastenkürzel" oeffnet die Uebersicht');
  await p.keyboard.press('Escape');

  // --- Mac-Darstellung ------------------------------------------------------------------------------------------------
  const m = await login(USER, true);
  await m.goto(BASE + '/dashboard'); await m.waitForTimeout(1200);
  await m.keyboard.press('Meta+/'); await m.waitForTimeout(300);
  const mrows = await m.evaluate(() => [...document.querySelectorAll('.kz-row')].map(r => r.querySelector('dd').textContent + ' = ' + r.querySelector('dt').textContent));
  const mrow = t => mrows.find(r => r.indexOf(t) === 0) || '';
  ok(mrows.length && /⌘K/.test(mrow('Schnellsuche')) && /⌘⌥N/.test(mrow('Neue leere Seite')) && /⌘⇧U/.test(mrow('Übergeordnete')), 'Mac: ⌘ ⌥ ⇧', [mrow('Schnellsuche'), mrow('Neue leere Seite')]);
  ok(/⌃⇧J/.test(mrow('Vorschau: nächster')), 'Mac: Peek mit ⌃ (Control)', mrow('Vorschau: nächster'));
  ok(!mrows.some(r => /Strg/.test(r)), 'Mac: kein "Strg"');
  await m.keyboard.press('Escape');
  await m.context().close();

  // --- Admin: "Admin-Bereich ↗" -------------------------------------------------------------------------------------
  if (E.SAGA_ADMIN_USER) {
    const a = await login(E.SAGA_ADMIN_USER, false);
    await a.goto(BASE + '/dashboard'); await a.waitForTimeout(1000);
    await a.click('#hk-user'); await a.waitForTimeout(250);
    const ai = await a.$$eval('.menu:not(.menu-sub) .menu-item', l => l.map(x => x.textContent));
    ok(ai.some(t => t.indexOf('Admin-Bereich ↗') >= 0), 'Admin: "Admin-Bereich ↗" im Kontomenue', ai);
    await a.context().close();
  }

  ok(!errs.length, 'keine Skriptfehler ' + errs.join(' | '));
  await b.close();
  const bad = res.filter(r => !r[0]).length; console.log('\n' + (res.length - bad) + '/' + res.length + ' ok'); console.log(bad ? 'FEHLER' : 'ALLES GRUEN'); process.exit(bad ? 1 : 0);
})();
