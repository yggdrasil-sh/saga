// Saga 2.5 (Design-Runde, UX-39) - Einstellungen mit Seitennavigation: Gruppen Konto (Profil & Sprache, Sicherheit,
// Mitteilungen) · Arbeitsbereich (Seiten, Kalender, Lesezeichen) · Verbindungen (Immich, Nextcloud, Apps & Geraete, API) ·
// Daten (Mein Backup) · Hilfe. Alle Bereiche erreichbar, aria-current, Fokus auf die Ueberschrift; Passwort/Passkeys unter
// "Sicherheit", Benachrichtigungen unter "Mitteilungen"; alte Deep-Links (?settings=, HKSettings.open, #hk-tab-*) und das
// Zahnrad der Glocke gehen weiter; am Handy Liste -> Bereich mit Zurueck, 44-px-Ziele.
//   SAGA_BASE=… SAGA_USER=… SAGA_PASS=… node tests/browser_einstellungen_nav.js
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const E = process.env, BASE = E.SAGA_BASE, USER = E.SAGA_USER, PASS = E.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const ALL = ['account', 'security', 'push', 'pages', 'calendar', 'bookmarks', 'immich', 'nextcloud', 'apps', 'api', 'backup', 'help'];
(async () => {
  const b = await chromium.launch({ channel: E.PW_CHANNEL || undefined }), res = [], errs = [];
  const ok = (c, m, x) => { res.push([!!c, m]); console.log((c ? 'OK   ' : 'FAIL ') + m + (c || x === undefined ? '' : ' ' + JSON.stringify(x))); };
  const ctx = await b.newContext({ viewport: { width: 1300, height: 860 }, locale: 'de-DE' });
  const p = await ctx.newPage(); p.on('pageerror', e => errs.push(String(e)));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  const state = () => p.evaluate(() => {
    const d = document.querySelector('.hk-dialog-set'); if (!d) return null;
    const cur = [...d.querySelectorAll('.hk-setnav [aria-current="page"]')].map(x => x.dataset.k);
    const vis = [...d.querySelectorAll('.hk-set')].filter(s => !s.hidden).map(s => (s.id || '').replace('hk-pane-', ''));
    const a = document.activeElement;
    return { cur, vis, focus: a ? a.id : null, list: d.classList.contains('nav-list') };
  });

  // --- Breit: Aufbau ----------------------------------------------------------------------------------------------------
  await p.goto(BASE + '/dashboard'); await p.waitForTimeout(1200);
  await p.click('#hk-user'); await p.click('.menu-item:has-text("Einstellungen")'); await p.waitForTimeout(900);
  const nav = await p.evaluate(() => {
    const n = document.querySelector('.hk-dialog-set nav.hk-setnav');
    return n && { label: n.getAttribute('aria-label'), groups: [...n.querySelectorAll('.hk-setnav-h')].map(h => h.textContent),
      keys: [...n.querySelectorAll('.hk-setnav-i')].map(x => x.dataset.k), labels: [...n.querySelectorAll('.hk-setnav-i')].map(x => x.textContent),
      tabs: !!document.querySelector('.hk-dialog-set .hk-tabs, .hk-dialog-set [role="tab"]') };
  });
  ok(nav && nav.label, 'Navigation als <nav> mit Namen', nav);
  ok(nav && ['Konto', 'Arbeitsbereich', 'Verbindungen', 'Daten'].every((g, i) => nav.groups[i] === g), 'Gruppen Konto · Arbeitsbereich · Verbindungen · Daten', nav && nav.groups);
  ok(nav && ALL.every(k => nav.keys.indexOf(k) >= 0) && nav.keys[nav.keys.length - 1] === 'help', 'alle Bereiche, Hilfe zuletzt', nav && nav.keys);
  ok(nav && nav.labels[0] === 'Profil & Sprache' && nav.labels.indexOf('Sicherheit') === 1 && nav.labels.indexOf('Mitteilungen') === 2, 'Konto: Profil & Sprache, Sicherheit, Mitteilungen', nav && nav.labels);
  ok(nav && !nav.tabs, 'keine Reiterleiste mehr');
  let s = await state();
  ok(s && s.cur.join() === 'account' && s.vis.join() === 'account', 'Start: Profil & Sprache', s);
  // jeden Bereich anklicken
  for (const k of ALL) {
    await p.click('#hk-tab-' + k); await p.waitForTimeout(120);
    s = await state();
    const visible = await p.evaluate(k => { const h = document.getElementById('hk-pane-h-' + k); return !!h && h.getClientRects().length > 0 && h.textContent.length > 0; }, k);
    ok(s.cur.join() === k && s.vis.join() === k && s.focus === 'hk-pane-h-' + k && visible, 'Bereich ' + k + ': aria-current, sichtbar, Fokus auf der Ueberschrift', s);
  }
  // Inhalte richtig zugeordnet
  await p.waitForTimeout(800);
  const where = await p.evaluate(() => {
    const t = id => (document.getElementById(id) || {}).textContent || '';
    return { accPw: /Passwort ändern/.test(t('hk-pane-account')), secPw: /Passwort ändern/.test(t('hk-pane-security')),
      notifyIn: (document.getElementById('hk-notify-set') || {}).closest ? document.getElementById('hk-notify-set').closest('.hk-set').id : null,
      lang: !!document.querySelector('#hk-pane-account #hk-lang') };
  });
  ok(where.secPw && !where.accPw, 'Passwort ändern steht unter Sicherheit, nicht unter Profil', where);
  ok(where.notifyIn === 'hk-pane-push', 'Benachrichtigungen je Art unter Mitteilungen', where);
  ok(where.lang, 'Sprache unter Profil & Sprache');
  // Tastatur: Pfeile in der Navigation
  await p.focus('#hk-tab-account'); await p.keyboard.press('ArrowDown');
  ok(await p.evaluate(() => document.activeElement.id) === 'hk-tab-security', 'Pfeil runter: naechster Eintrag');
  await p.keyboard.press('End'); ok(await p.evaluate(() => document.activeElement.id) === 'hk-tab-help', 'Ende: letzter Eintrag');
  await p.keyboard.press('Enter'); await p.waitForTimeout(150);
  s = await state(); ok(s.cur.join() === 'help' && s.focus === 'hk-pane-h-help', 'Enter waehlt, Fokus auf Ueberschrift', s);
  await p.keyboard.press('Escape'); await p.waitForTimeout(200);
  ok(!(await p.$('.hk-dialog-set')), 'Esc schliesst');

  // --- Deep-Links --------------------------------------------------------------------------------------------------------
  for (const [k, want] of [['calendar', 'calendar'], ['immich', 'immich'], ['nextcloud', 'nextcloud'], ['api', 'api'], ['bookmarks', 'bookmarks'], ['security', 'security'], ['notify', 'push'], ['account', 'account']]) {
    await p.evaluate(k => window.HKSettings.open(k), k); await p.waitForTimeout(300);
    s = await state(); ok(s && s.cur.join() === want && s.vis.join() === want, 'HKSettings.open(' + k + ') -> ' + want, s);
  }
  await p.evaluate(() => window.HKSettings.open('lang')); await p.waitForTimeout(1000);
  ok(await p.evaluate(() => document.activeElement && document.activeElement.id) === 'hk-lang', "HKSettings.open('lang'): Sprachauswahl fokussiert");
  await p.keyboard.press('Escape');
  await p.goto(BASE + '/?settings=backup'); await p.waitForTimeout(1500);
  s = await state(); ok(s && s.vis.join() === 'backup', '/?settings=backup oeffnet Mein Backup', s);
  ok(!/settings=/.test(p.url()), 'Parameter danach aus der Adresse');
  await p.goto(BASE + '/notes?settings=api'); await p.waitForTimeout(2000);
  s = await state(); ok(s && s.vis.join() === 'api', '/notes?settings=api oeffnet API', s);
  await p.click('#hk-tab-help'); await p.waitForTimeout(200);
  ok(await p.locator('#hk-pane-help').isVisible(), '#hk-tab-help / #hk-pane-help wie bisher');
  await p.keyboard.press('Escape'); await p.waitForTimeout(200);
  // Glocke -> Zahnrad: Mitteilungen mit den Schaltern
  await p.goto(BASE + '/dashboard'); await p.waitForTimeout(1200);
  if (await p.$('#hk-bell')) {
    await p.click('#hk-bell'); await p.waitForTimeout(500);
    const gear = await p.$('.lv-gear');
    if (gear) {
      await gear.click(); await p.waitForTimeout(1500);
      s = await state();
      const f = await p.evaluate(() => { const n = document.getElementById('hk-notify-set'), a = document.activeElement; return !!n && n.contains(a) && n.getClientRects().length > 0; });
      ok(s && s.vis.join() === 'push' && f, 'Glocke -> Zahnrad: Mitteilungen, Schalter fokussiert', s);
      await p.keyboard.press('Escape');
    } else ok(false, 'Zahnrad in der Glocke nicht gefunden');
  }

  // --- Handy ------------------------------------------------------------------------------------------------------------
  await p.setViewportSize({ width: 390, height: 800 }); await p.waitForTimeout(300);
  await p.evaluate(() => window.HKSettings.open()); await p.waitForTimeout(700);
  s = await state(); ok(s && s.list, 'Handy: erst die Liste', s);
  const m1 = await p.evaluate(() => {
    const d = document.querySelector('.hk-dialog-set'), r = d.getBoundingClientRect();
    const items = [...d.querySelectorAll('.hk-setnav-i')].map(x => x.getBoundingClientRect().height);
    return { min: Math.min.apply(null, items), body: d.querySelector('.hk-set-main > .hk-dlg-body').getClientRects().length, w: r.width, vw: innerWidth,
      over: document.documentElement.scrollWidth > innerWidth };
  });
  ok(m1.min >= 44 && m1.body === 0 && !m1.over, 'Handy: Eintraege mind. 44 px, Inhalt verborgen, kein Ueberlauf', m1);
  ok(await p.evaluate(() => document.activeElement && document.activeElement.id) === 'hk-tab-account', 'Handy: Fokus auf dem ersten Eintrag');
  await p.click('#hk-tab-calendar'); await p.waitForTimeout(300);
  s = await state();
  const m2 = await p.evaluate(() => { const bk = document.querySelector('.hk-set-back'), n = document.querySelector('.hk-setnav'); return { back: bk.getClientRects().length ? bk.getBoundingClientRect().height : 0, nav: n.getClientRects().length }; });
  ok(!s.list && s.vis.join() === 'calendar' && s.focus === 'hk-pane-h-calendar' && m2.back >= 44 && m2.nav === 0, 'Handy: Bereich mit Zurueck (44 px), Liste weg', { s, m2 });
  await p.click('.hk-set-back'); await p.waitForTimeout(200);
  s = await state(); ok(s.list && s.focus === 'hk-tab-calendar', 'Handy: Zurueck zur Liste, Fokus auf dem Eintrag', s);
  await p.keyboard.press('Escape'); await p.waitForTimeout(200);
  await p.evaluate(() => window.HKSettings.open('immich')); await p.waitForTimeout(500);
  s = await state(); ok(s && !s.list && s.vis.join() === 'immich', 'Handy: Deep-Link oeffnet den Bereich direkt', s);
  await p.keyboard.press('Escape');

  ok(!errs.length, 'keine Skriptfehler ' + errs.join(' | '));
  await b.close();
  const bad = res.filter(r => !r[0]).length; console.log('\n' + (res.length - bad) + '/' + res.length + ' ok'); console.log(bad ? 'FEHLER' : 'ALLES GRUEN'); process.exit(bad ? 1 : 0);
})();
