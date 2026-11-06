// Saga 2.5 (Design-Runde, UX-10) - Schnellsuche (Strg/⌘+K) auf allen Seiten mit globaler Seitenleiste: auf Heute und
// /chats oeffnet sie sich als Overlay (kein Sprung nach /notes?qs=1), mit Seiten, "Zuletzt geoeffnet", ">"-Befehlen,
// "+ Neue Seite" und - nur mit SearXNG - "Im Web suchen". Esc schliesst, der Fokus geht an den Ausloeser zurueck, ohne
// vorherigen Fokus NICHT an einen laengst geschlossenen Menueknopf. Seiten oeffnen sich auf /notes#id.
//   SAGA_BASE=… SAGA_USER=… SAGA_PASS=… [SAGA_WEB=1: Server mit SEARXNG_URL + tests/fakesearx.py] node tests/browser_schnellsuche_global.js
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS, WEB = !!process.env.SAGA_WEB;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
(async () => {
  const b = await chromium.launch({ channel: process.env.PW_CHANNEL || undefined }), res = [], errs = [];
  const ok = (c, m, x) => { res.push([!!c, m]); console.log((c ? 'OK   ' : 'FAIL ') + m + (c || x === undefined ? '' : ' ' + JSON.stringify(x))); };
  const ctx = await b.newContext({ viewport: { width: 1300, height: 860 }, locale: 'de-DE' });
  await ctx.route('https://example.org/**', r => r.fulfill({ status: 200, contentType: 'text/html', body: '<title>Treffer</title>ok' }));
  const p = await ctx.newPage(); p.on('pageerror', e => errs.push(String(e)));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  const api = (m, u, body) => p.evaluate(([m, u, body]) => fetch(u, { method: m, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Saga': '1' },
    body: body ? JSON.stringify(body) : undefined }).then(r => r.json()), [m, u, body]);
  const stamp = Date.now().toString(36);
  const pg = await api('POST', '/api/notes', { title: 'Wolkenkuckucksheim ' + stamp });
  ok(pg && pg.id, 'Testseite angelegt');
  const qsOpen = () => p.evaluate(() => { const q = document.getElementById('pg-qs'); return !!q && !q.hidden; });
  const active = () => p.evaluate(() => { const a = document.activeElement; return a ? (a.id || a.tagName) : null; });
  const rows = () => p.$$eval('#pg-qs-list .picker-item', l => l.map(x => x.textContent));

  // --- Heute: Overlay statt Neuladen, Fokus-Rueckgabe ohne vorherigen Fokus ------------------------------------------
  await p.goto(BASE + '/dashboard'); await p.waitForTimeout(1200);
  // Kontomenue oeffnen und wieder schliessen, Fokus wegnehmen, kurz warten: das Menue ist "laengst" zu
  await p.click('#hk-user'); await p.waitForTimeout(200); await p.keyboard.press('Escape'); await p.waitForTimeout(100);
  await p.evaluate(() => document.activeElement && document.activeElement.blur());
  await p.waitForTimeout(2800);
  const url0 = p.url();
  await p.keyboard.press('Control+k'); await p.waitForTimeout(500);
  ok(await qsOpen(), 'Heute: Strg+K oeffnet die Schnellsuche als Overlay');
  ok(p.url() === url0, 'Heute: kein Sprung nach /notes', p.url());
  ok(await active() === 'pg-qs-q', 'Fokus im Suchfeld');
  ok(await p.evaluate(() => document.querySelector('#pg-qs .qs-box').getAttribute('aria-modal') === 'true'), 'Dialog mit aria-modal');
  const r0 = await rows();
  ok(r0.some(t => t.indexOf('Wolkenkuckucksheim ' + stamp) >= 0), 'leere Suche: zuletzt bearbeitete Seite in der Liste', r0);
  // Tab bleibt im Dialog (Fokusfalle)
  await p.keyboard.press('Tab'); await p.keyboard.press('Tab'); await p.keyboard.press('Tab');
  ok(await p.evaluate(() => document.getElementById('pg-qs').contains(document.activeElement)), 'Tab bleibt im Dialog');
  await p.keyboard.press('Escape'); await p.waitForTimeout(200);
  ok(!(await qsOpen()), 'Esc schliesst');
  ok(await active() !== 'hk-user', 'Fokus nicht am laengst geschlossenen Kontomenue-Knopf', await active());
  // Befehle
  await p.keyboard.press('Control+k'); await p.waitForTimeout(400);
  await p.keyboard.type('>'); await p.waitForTimeout(200);
  const cmds = await rows();
  ok(cmds.some(t => /Neue Seite/.test(t)) && cmds.some(t => /Papierkorb/.test(t)) && cmds.some(t => /Einstellungen/.test(t)), '">" zeigt Befehle', cmds);
  ok(!cmds.some(t => /⌂ Heute/.test(t)), 'auf Heute kein Befehl "Heute"');
  await p.keyboard.press('Escape');
  // Ohne SearXNG keine Web-Zeile, mit SearXNG Treffer im Overlay
  await p.keyboard.press('Control+k'); await p.waitForTimeout(300);
  await p.keyboard.type('zzwolke'); await p.waitForTimeout(500);
  const r1 = await rows();
  ok(r1.some(t => t.indexOf('Neue Seite „zzwolke“') >= 0), '"+ Neue Seite" bei unbekanntem Begriff', r1);
  const webRow = r1.find(t => t.indexOf('Im Web suchen') >= 0);
  if (!WEB) ok(!webRow, 'ohne SearXNG: keine Web-Zeile');
  else {
    ok(!!webRow, 'mit SearXNG: Zeile "Im Web suchen" auf Heute');
    await p.keyboard.press('ArrowDown'); await p.keyboard.press('Enter'); await p.waitForTimeout(1200);
    const hits = await p.$$eval('#pg-qs-list .qs-web', l => l.map(x => x.textContent));
    ok(hits.length === 3 && await qsOpen(), 'Websuche: Treffer im Overlay, bleibt offen', hits);
  }
  await p.keyboard.press('Escape'); await p.waitForTimeout(150);
  // Seite oeffnen -> /notes#id
  await p.keyboard.press('Control+k'); await p.waitForTimeout(300);
  await p.keyboard.type('Wolkenkuckuck'); await p.waitForTimeout(400);
  await Promise.all([p.waitForURL(/\/notes#/, { timeout: 8000 }).catch(() => {}), p.keyboard.press('Enter')]);
  ok(new RegExp('/notes#' + pg.id + '$').test(p.url()), 'Enter oeffnet die Seite auf /notes#id', p.url());
  await p.waitForTimeout(1200);
  // /notes: dieselbe Oberflaeche
  await p.keyboard.press('Escape');
  await p.evaluate(() => document.activeElement && document.activeElement.blur());
  await p.keyboard.press('Control+k'); await p.waitForTimeout(400);
  ok(await qsOpen(), '/notes: Strg+K oeffnet die Schnellsuche');
  ok((await rows()).some(t => t.indexOf('Wolkenkuckucksheim ' + stamp) >= 0), '/notes: "Zuletzt geoeffnet" mit der Seite');
  await p.keyboard.press('Escape'); await p.waitForTimeout(150);
  ok(!(await qsOpen()), '/notes: Esc schliesst');

  // --- Chats: Overlay, Rueckgabe an den Ausloeser, Knopf in der Seitenleiste ------------------------------------------
  await p.goto(BASE + '/chats'); await p.waitForTimeout(1500);
  if (await p.$('.ct-setup-go')) {                     // frisches Konto: Chat-Schluessel einrichten (Dialog davor)
    await p.click('.ct-setup-go'); await p.waitForSelector('#ct-rk'); const rk = (await p.textContent('#ct-rk')).trim();
    await p.click('.hk-actions .btn-primary'); await p.fill('.ct-rk-last', rk.slice(-4)); await p.click('.ct-setup-done');
    await p.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 60000 }); await p.waitForTimeout(600);
  }
  const hasSide = await p.evaluate(() => !!document.getElementById('sh-qs-btn'));
  if (!hasSide) ok(false, '/chats: Seitenleiste mit Schnellsuche fehlt');
  else {
    await p.evaluate(() => { const x = document.querySelector('.pg-mainnav a'); x.focus(); });
    const before = await p.evaluate(() => document.activeElement.getAttribute('href'));
    const url1 = p.url();
    await p.keyboard.press('Control+k'); await p.waitForTimeout(500);
    ok(await qsOpen() && p.url() === url1, '/chats: Strg+K oeffnet das Overlay (kein Neuladen)');
    await p.keyboard.type('>'); await p.waitForTimeout(200);
    const c2 = await rows();
    ok(c2.some(t => /Heute/.test(t)) && !c2.some(t => /💬 Chats/.test(t)), '/chats: Befehle mit "Heute", ohne "Chats"', c2);
    await p.keyboard.press('Escape'); await p.waitForTimeout(200);
    ok(await p.evaluate(() => document.activeElement && document.activeElement.getAttribute('href')) === before, '/chats: Fokus zurueck an den Ausloeser');
    await p.click('#sh-qs-btn'); await p.waitForTimeout(400);
    ok(await qsOpen() && p.url() === url1, '/chats: Knopf "Schnellsuche" der Seitenleiste oeffnet das Overlay');
    await p.keyboard.press('Escape'); await p.waitForTimeout(200);
    ok(await active() === 'sh-qs-btn', 'Fokus zurueck an den Knopf');
  }

  // --- Neue Seite aus der Schnellsuche (Heute) --------------------------------------------------------------------------
  await p.goto(BASE + '/dashboard'); await p.waitForTimeout(1000);
  await p.keyboard.press('Control+k'); await p.waitForTimeout(300);
  await p.keyboard.type('Neu aus Heute ' + stamp); await p.waitForTimeout(500);
  const nIdx = (await rows()).findIndex(t => t.indexOf('Neue Seite „Neu aus Heute') >= 0);
  if (nIdx >= 0) await p.click('#pg-qs-list .picker-item >> nth=' + nIdx);
  await p.waitForURL(/\/notes#\d+/, { timeout: 8000 }).catch(() => {});
  const list = await api('GET', '/api/notes');
  ok((Array.isArray(list) ? list : []).some(n => n.title === 'Neu aus Heute ' + stamp) && /\/notes#\d+/.test(p.url()), '"+ Neue Seite" legt an und oeffnet sie', p.url());

  // --- Andere Dialoge offen: Strg+K stoert nicht -------------------------------------------------------------------------
  await p.goto(BASE + '/dashboard'); await p.waitForTimeout(1000);
  await p.evaluate(() => window.HKSettings.open()); await p.waitForTimeout(400);
  await p.keyboard.press('Control+k'); await p.waitForTimeout(300);
  ok(!(await qsOpen()), 'bei offenen Einstellungen oeffnet Strg+K nichts darueber');

  ok(!errs.length, 'keine Skriptfehler ' + errs.join(' | '));
  await b.close();
  const bad = res.filter(r => !r[0]).length; console.log('\n' + (res.length - bad) + '/' + res.length + ' ok'); console.log(bad ? 'FEHLER' : 'ALLES GRUEN'); process.exit(bad ? 1 : 0);
})();
