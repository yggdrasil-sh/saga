// Saga 2.5 (Design-Runde 12) - Tab-Leiste unten am Handy: Heute · Seiten · Suchen · Chats (· KI), aktuelle Ansicht markiert,
// "Seiten" oeffnet auf /notes die Schublade, "Suchen" die Schnellsuche; am Desktop unsichtbar; kein waagrechter Ueberlauf.
//   SAGA_BASE=http://127.0.0.1:8099 SAGA_USER=konto SAGA_PASS=... [PLAYWRIGHT=...] node tests/browser_tableiste.js
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
(async () => {
  const b = await chromium.launch(), res = [], errs = [];
  const ok = (c, m) => { res.push([!!c, m]); console.log((c ? 'OK   ' : 'FAIL ') + m); };
  const ctx = await b.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, locale: 'de-DE' });
  const p = await ctx.newPage(); p.on('pageerror', e => errs.push(String(e)));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  const st = () => p.evaluate(() => { const n = document.getElementById('mob-tabs'); if (!n) return null; const r = n.getBoundingClientRect();
    return { vis: getComputedStyle(n).display !== 'none', bottom: Math.round(innerHeight - r.bottom), cur: (n.querySelector('[aria-current=page] span') || {}).textContent || '',
      tabs: [...n.querySelectorAll('.mob-tab:not([hidden])')].length, over: document.documentElement.scrollWidth - innerWidth }; });
  await p.goto(BASE + '/dashboard'); await p.waitForTimeout(1200);
  let s = await st(); ok(s && s.vis && s.bottom === 0, 'Heute: Leiste unten sichtbar'); ok(s && s.cur === 'Heute', 'Heute: markiert');
  ok(s && s.tabs >= 3, 'Heute: mind. drei Reiter'); ok(s && s.over <= 0, 'Heute: kein Ueberlauf');
  await p.goto(BASE + '/notes'); await p.waitForTimeout(1500);
  s = await st(); ok(s && s.vis && s.cur === 'Seiten', 'Seiten: sichtbar und markiert'); ok(s && s.over <= 0, 'Seiten: kein Ueberlauf');
  await p.click('#mob-tabs [data-act=pages]'); await p.waitForTimeout(400);
  ok(await p.evaluate(() => document.body.classList.contains('side-open')), 'Seiten: oeffnet die Schublade');
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  await p.click('#mob-tabs [data-act=search]'); await p.waitForTimeout(500);
  ok(await p.evaluate(() => { const q = document.querySelector('.qs'); return !!q && !q.hidden && getComputedStyle(q).display !== 'none'; }), 'Suchen: oeffnet die Schnellsuche');
  await p.keyboard.press('Escape');
  await p.setViewportSize({ width: 1300, height: 860 }); await p.waitForTimeout(300);
  s = await st(); ok(s && !s.vis, 'Desktop: Leiste unsichtbar');
  ok(!errs.length, 'keine Skriptfehler ' + errs.join(' | '));
  await b.close();
  const bad = res.filter(r => !r[0]).length; console.log('\n' + (res.length - bad) + '/' + res.length + ' ok'); process.exit(bad ? 1 : 0);
})();
