// Saga 2.5 (Design-Runde 05) - "Heute" (/dashboard) als Tagesstart: Begruessung, Lagezeile aus echten Daten,
// Schnellaktionen, "Weitermachen" mit zuletzt geoeffneten Seiten, keine Kontobox/eigene Suche, Skip-Link + H1,
// kein waagrechter Ueberlauf auf dem Handy (390 px), keine Skriptfehler. KI-Knopf nur, wenn der Assistent an ist
// (Status per Route vorgetaeuscht).
//   SAGA_BASE=http://127.0.0.1:8099 SAGA_USER=konto SAGA_PASS=... [PLAYWRIGHT=...] node tests/browser_heute.js
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const E = process.env, BASE = E.SAGA_BASE || 'http://127.0.0.1:8099', USER = E.SAGA_USER, PASS = E.SAGA_PASS;
if (!USER || !PASS) { console.error('SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const R = []; const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x === undefined ? '' : x))); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(150); } }

(async () => {
  const br = await chromium.launch(E.PW_CHANNEL ? { channel: E.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1440, height: 900 }, locale: 'de-DE' });
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    const d = await r.json().catch(() => ({})); return { s: r.status, d: d };
  }, [m, url, b]);

  // Testdaten: zwei Seiten, eine ueberfaellige Aufgabe; die Seiten als "zuletzt geoeffnet" (wie static/pages.js)
  await p.goto(BASE + '/dashboard');
  const tag = 'Heute-Test ' + Date.now().toString(36);
  const a1 = (await api('POST', '/api/notes', { title: tag + ' A' })).d.id, a2 = (await api('POST', '/api/notes', { title: tag + ' B' })).d.id;
  const d = new Date(Date.now() - 3 * 86400000), due = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  const tk = (await api('POST', '/api/tasks', { text: tag + ' alt', due: due })).d.id;
  await p.evaluate((ids) => localStorage.setItem('hk-pg-recent', JSON.stringify(ids)), [a2, a1]);

  await p.goto(BASE + '/dashboard'); await p.waitForTimeout(1500);
  ok('Titel "Heute · …"', /^Heute · /.test(await p.title()), await p.title());
  const h = await p.evaluate(() => ({
    h1: [...document.querySelectorAll('h1')].map((x) => x.textContent.trim()),
    banner: document.querySelectorAll('header, [role=banner]').length,
    skip: !!document.querySelector('a.skip-link[href="#heute"]'),
    greet: (document.getElementById('hz-greet') || {}).textContent || '',
    lage: (document.getElementById('hz-lage') || {}).textContent || '',
    acts: [...document.querySelectorAll('.hz-act:not([hidden])')].map((x) => x.id),
    q: !!document.getElementById('q'), acct: !!document.querySelector('.acct-foot'), hint: !!document.querySelector('main .hint'),
    dashHead: !!document.querySelector('.dash-head'), bells: document.querySelectorAll('#hk-bell').length,
    cards: [...document.querySelectorAll('#hz-cards .hz-card')].map((x) => ({ t: x.querySelector('.hz-card-t').textContent, href: x.getAttribute('href') })),
    ai: !document.getElementById('hz-ai').hidden
  }));
  ok('genau eine H1 "Heute"', h.h1.length === 1 && h.h1[0] === 'Heute', h.h1);
  ok('eine Banner-Kopfzeile', h.banner === 1, h.banner);
  ok('Skip-Link "Zum Inhalt"', h.skip);
  ok('Begruessung mit Vorname', /^Guten (Morgen|Tag|Abend), \S+$/.test(h.greet), h.greet);
  ok('Lagezeile: ueberfaellige Aufgaben', /\d+ Aufgaben? überfällig/.test(h.lage), h.lage);
  ok('Schnellaktionen da (Neue Seite, Vorlage, Aufgabe, Termin)', ['hz-new', 'hz-tpl', 'hz-task', 'hz-event'].every((x) => h.acts.includes(x)), h.acts);
  ok('KI-Knopf aus, wenn der Assistent aus ist', !h.ai || h.acts.includes('hz-ai'));
  ok('Weitermachen: zuletzt geoeffnete Seiten in Reihenfolge', h.cards.length >= 2 && h.cards[0].href === '/notes#' + a2 && h.cards[1].href === '/notes#' + a1, h.cards);
  ok('keine eigene Suche, keine Kontobox, keine Kuerzelzeile, kein alter Kopf', !h.q && !h.acct && !h.hint && !h.dashHead, h);
  ok('nur eine Glocke', h.bells <= 1, h.bells);

  // Admin-Bereich im Kontomenue (nur Admins: data-admin am Knopf)
  const adm = await p.evaluate(() => document.getElementById('hk-user').dataset.admin || '');
  await p.click('#hk-user'); await p.waitForTimeout(300);
  const items = await p.evaluate(() => [...document.querySelectorAll('.menu .menu-item')].map((x) => x.textContent.trim()));
  ok('Kontomenue: Admin-Bereich genau dann, wenn Admin', items.some((x) => /Admin-Bereich/.test(x)) === !!adm, { adm, items });
  await p.keyboard.press('Escape');

  // "Aufgabe" fokussiert das Schnellfeld; Platzhalter kurz
  await p.click('#hz-task'); await p.waitForTimeout(200);
  const foc = await p.evaluate(() => document.activeElement && document.activeElement.id);
  ok('Schnellaktion Aufgabe -> Aufgaben-Eingabe', foc === 'rail-text' || foc === 'task-text', foc);
  ok('Platzhalter "Aufgabe …"', (await p.getAttribute('#rail-text', 'placeholder')) === 'Aufgabe …');
  await p.evaluate(() => document.activeElement.blur());

  // Tastatur: "/" -> Suche der Seitenleiste; Ziffern der Lesezeichen nur bei Hover/Fokus sichtbar
  await p.keyboard.press('/'); await p.waitForTimeout(150);
  ok('"/" fokussiert die Seitensuche', await p.evaluate(() => document.activeElement && document.activeElement.id === 'sh-search'));
  await p.evaluate(() => document.activeElement.blur());

  // KI an (Status vorgetaeuscht): Knopf erscheint, Klick oeffnet das Panel (Frag deine Seiten)
  await p.route('**/api/ai/status', (r) => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ enabled: true, persona: { name: 'Testa', avatar: null }, notice: '', quota: 0, used: 0 }) }));
  await p.goto(BASE + '/dashboard'); await p.waitForTimeout(1500);
  ok('KI an: Knopf "Frage Testa"', await p.evaluate(() => { const b = document.getElementById('hz-ai'); return !b.hidden && /Frage Testa/.test(b.textContent); }));
  await p.click('#hz-ai'); await p.waitForTimeout(400);
  ok('KI-Panel geht auf (ohne Seite: Reiter "Frag deine Seiten")', await p.evaluate(() => { const a = document.getElementById('ai-panel'); return !!a && !a.hidden; }));
  await p.unroute('**/api/ai/status');

  // "Aus Vorlage …" -> /notes?gallery=1 oeffnet die Vorlagengalerie
  await p.goto(BASE + '/notes?gallery=1');
  const gal = await until(() => p.evaluate(() => { const m = document.getElementById('pg-modal'); return !!m && !m.hidden && /Vorlagengalerie/.test(m.textContent); }), 8000);
  ok('/notes?gallery=1 oeffnet die Vorlagengalerie', gal);
  ok('... Parameter aus der Adresse entfernt', !/gallery/.test(p.url()), p.url());

  // Handy 390 px: einspaltig, kein waagrechter Ueberlauf, Kopf oben sichtbar
  for (const scheme of ['light', 'dark']) {
    const m = await br.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, locale: 'de-DE', colorScheme: scheme, storageState: await ctx.storageState() });
    const q = await m.newPage(); q.on('pageerror', (e) => errs.push(e.message));
    await q.goto(BASE + '/dashboard'); await q.waitForTimeout(1500);
    const r = await q.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
      head: getComputedStyle(document.querySelector('.np-head')).display !== 'none',
      cardsW: (document.querySelector('#hz-cards') || {}).scrollWidth, cardsC: (document.querySelector('#hz-cards') || {}).clientWidth }));
    ok(`Handy (${scheme}): kein waagrechter Ueberlauf`, r.sw <= r.cw && r.cardsW <= r.cardsC, r);
    ok(`Handy (${scheme}): Kopfzeile oben sichtbar`, r.head, r);
    await m.close();
  }

  // Aufraeumen
  await p.goto(BASE + '/dashboard');
  await api('DELETE', '/api/tasks/' + tk);
  for (const id of [a1, a2]) await api('DELETE', '/api/notes/' + id);
  ok('keine Skriptfehler (pageerror)', !errs.length, errs);
  await br.close();
  const bad = R.filter((x) => !x[1]); console.log(`\n${R.length - bad.length}/${R.length} ok`); process.exit(bad.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
