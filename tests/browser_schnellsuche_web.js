// Saga 2.5 (Design-Runde) - Websuche in der Schnellsuche (frueher Suchfeld auf "Heute"): Zeile "Im Web suchen: …",
// Enter zeigt die SearXNG-Treffer in der Liste (Host, Ausschnitt, Sofortantwort), javascript:-Links fallen weg,
// "Mehr laden" haengt Seite 2 an, Tippen fuehrt zurueck zu den Seiten. Ohne SEARXNG_URL keine Zeile.
//   Server mit SEARXNG_URL=http://127.0.0.1:<port> SEARXNG_SECRET=… und Attrappe: python3 tests/fakesearx.py <port>
//   SAGA_BASE=… SAGA_USER=… SAGA_PASS=… [SAGA_NOWEB=1: Server ohne SearXNG] node tests/browser_schnellsuche_web.js
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS, NOWEB = !!process.env.SAGA_NOWEB;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
(async () => {
  const b = await chromium.launch(), res = [], errs = [];
  const ok = (c, m) => { res.push([!!c, m]); console.log((c ? 'OK   ' : 'FAIL ') + m); };
  const ctx = await b.newContext({ viewport: { width: 1300, height: 860 }, locale: 'de-DE' });
  await ctx.route('https://example.org/**', r => r.fulfill({ status: 200, contentType: 'text/html', body: '<title>Treffer</title>ok' }));   // nichts nach aussen
  const p = await ctx.newPage(); p.on('pageerror', e => errs.push(String(e)));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  await p.goto(BASE + '/notes'); await p.waitForTimeout(1500);
  await p.keyboard.press('Control+k'); await p.waitForTimeout(300);
  await p.fill('#pg-qs-q', 'zzwolke'); await p.waitForTimeout(500);
  const rows = () => p.$$eval('#pg-qs-list .picker-item', l => l.map(x => x.textContent));
  const webRow = (await rows()).find(t => t.indexOf('Im Web suchen') >= 0);
  if (NOWEB) { ok(!webRow, 'ohne SearXNG: keine Web-Zeile'); }
  else {
    ok(!!webRow, 'Zeile „Im Web suchen“ vorhanden');
    // keine Titel-Treffer -> Auswahl steht auf "+ Neue Seite"; ein Schritt nach unten = Websuche
    await p.keyboard.press('ArrowDown'); await p.keyboard.press('Enter'); await p.waitForTimeout(1200);
    const r1 = await p.evaluate(() => ({ open: !document.getElementById('pg-qs').hidden,
      hits: [...document.querySelectorAll('#pg-qs-list .qs-web')].map(x => x.textContent), ans: (document.querySelector('.qs-web-answer') || {}).textContent || '',
      on: (document.querySelector('#pg-qs-list .picker-item.on') || {}).textContent || '' }));
    ok(r1.open, 'Schnellsuche bleibt offen');
    ok(r1.hits.length === 3, 'drei Treffer (javascript:-Link weg) ' + r1.hits.length);
    ok(r1.hits[0] && r1.hits[0].indexOf('example.org') >= 0, 'Treffer mit Host');
    ok(r1.ans.indexOf('Antwort zu zzwolke') >= 0, 'Sofortantwort sichtbar');
    ok(r1.on.indexOf('Treffer 1') >= 0, 'erster Treffer ausgewaehlt');
    await p.waitForTimeout(800);                       // Server-Ratelimit 0,7 s
    await p.click('.qs-web-more'); await p.waitForTimeout(1200);
    ok((await p.$$('#pg-qs-list .qs-web')).length === 6, '„Mehr laden“ haengt Seite 2 an');
    const [pop] = await Promise.all([p.context().waitForEvent('page', { timeout: 5000 }).catch(e => { console.log('kein Tab', e.message.split('\n')[0]); return null; }), p.click('#pg-qs-list .qs-web')]); if (pop) await pop.waitForURL(/example/, { timeout: 3000 }).catch(() => {});
    ok(pop && pop.url().indexOf('https://example.org/') === 0, 'Klick oeffnet den Treffer im neuen Tab'); if (pop) await pop.close();
    await p.fill('#pg-qs-q', 'zzwolkeX'); await p.waitForTimeout(400);
    ok(!(await p.$('#pg-qs-list .qs-web')) && (await rows()).some(t => t.indexOf('Im Web suchen') >= 0), 'Tippen fuehrt zurueck zur Seitensuche');
    await p.keyboard.press('Escape'); await p.waitForTimeout(200);
    ok(await p.evaluate(() => document.getElementById('pg-qs').hidden), 'Esc schliesst');
  }
  ok(!errs.length, 'keine Skriptfehler ' + errs.join(' | '));
  await b.close();
  const bad = res.filter(r => !r[0]).length; console.log('\n' + (res.length - bad) + '/' + res.length + ' ok'); process.exit(bad ? 1 : 0);
})();
