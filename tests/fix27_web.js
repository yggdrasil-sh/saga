// Fix-Runde 27.10. (Galerie-Testrunde), Web-Teile: #1 Board-Ziehen mit echter Maus schreibt nichts ins Suchfeld,
// weitere Funde siehe Abschnitte. Server mit echter Galerie (gallery/).
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> CK2=<Cookie hk-test-b> node tests/fix27_web.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const shot = (p, n) => process.env.SHOTS ? p.screenshot({ path: process.env.SHOTS + '/' + n + '.png' }) : null;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(p, fn, arg, ms = 15000) {   // ohne waitForFunction (CSP ohne 'unsafe-eval')
  for (const t0 = Date.now(); Date.now() - t0 < ms; await sleep(100)) if (await p.evaluate(fn, arg)) return true;
  return false;
}
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function page(ck, opt) {
    const ctx = await br.newContext(Object.assign({ viewport: { width: 1440, height: 900 }, locale: 'de-DE' }, opt || {}));
    await ctx.addCookies([{ name: 'saga', value: ck, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = [];
    p.on('pageerror', (e) => p.errs.push(e.message));
    await p.goto(BASE + '/notes');
    p.api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
      const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
      const j = await r.json().catch(() => ({})); if (j && typeof j === 'object' && !Array.isArray(j)) j._st = r.status; return j;
    }, [m, url, b]);
    p.open = async (id) => { await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await sleep(1500); };
    return p;
  }
  const p = await page(CK);
  const AP = await p.api('POST', '/api/gallery/aufgaben-projekte/use', { examples: true });
  ok('Vorlage Aufgaben & Projekte angelegt', AP.root > 0, AP);

  // --- #1: Board-Karte mit echter Maus ziehen (eingebettete Datenbank auf der Startseite) ---------------------------
  {
    await p.open(AP.root);
    await until(p, () => document.querySelectorAll('.embed-db .board-col').length > 2);
    const cols = p.locator('.embed-db .board-col');
    const card = cols.first().locator('.board-card').first(), title = (await card.locator('.board-title').innerText()).trim();
    await card.scrollIntoViewIfNeeded();
    const a = await card.boundingBox(), t = await cols.last().boundingBox();
    await p.mouse.move(a.x + 20, a.y + 10); await p.mouse.down();
    await p.mouse.move(a.x + 40, a.y + 20, { steps: 5 });
    await p.mouse.move(t.x + t.width / 2, t.y + 60, { steps: 15 }); await p.mouse.up();
    const moved = await until(p, (tt) => [...document.querySelectorAll('.embed-db .board-col')].pop().textContent.includes(tt), title, 8000);
    ok('#1: Karte ist in der Zielspalte', moved, title);
    const st = await p.evaluate(() => [...document.querySelectorAll('.db-search')].map((i) => ({ v: i.value, f: document.activeElement === i })));
    ok('#1: Suchfeld leer und nicht fokussiert', st.every((x) => !x.v && !x.f), st);
    ok('#1: keine Zahl im Editor eingefuegt', !(await p.evaluate(() => /^\d+$/.test((document.activeElement && document.activeElement.value) || ''))));
  }

  // --- #2: Widget-Einstellungen in einer 4er-Spaltenreihe (Mein Tag) als Seitenleiste ----------------------------------
  const MT = await p.api('POST', '/api/gallery/mein-tag/use', { examples: true });
  ok('Vorlage Mein Tag angelegt', MT.root > 0, MT);
  {
    await p.open(MT.root);
    await until(p, () => document.querySelectorAll('.cols .wg-blk .wg').length >= 3);
    const narrow = p.locator('.cols .wg-blk').filter({ has: p.locator('.wg[data-kind="quote"]') }).first();
    const bw = (await narrow.boundingBox()).width;
    await narrow.locator('.wg').click();
    await until(p, () => !!document.querySelector('.wg-panel'));
    const info = await p.evaluate(() => {
      const pn = document.querySelector('.wg-panel'), r = pn.getBoundingClientRect();
      const ins = [...pn.querySelectorAll('input[type=text], select, textarea')].map((x) => Math.round(x.getBoundingClientRect().width));
      return { float: pn.classList.contains('wg-panel-float'), w: Math.round(r.width), minIn: Math.min.apply(null, ins), inBody: pn.parentNode === document.body,
        mark: !!document.querySelector('.wg-blk.wg-editing'), vis: r.right <= innerWidth && r.left >= 0,
        free: (() => { const w = document.querySelector('.wg-blk.wg-editing').getBoundingClientRect(); return w.right <= r.left || w.left >= r.right; })() };
    });
    ok(`#2: schmale Spalte (${Math.round(bw)} px) -> Panel als Seitenleiste`, info.float && info.inBody && info.mark && info.vis && info.free, info);
    await shot(p, '2-panel');
    ok(`#2: Eingabefelder breit genug (${info.minIn} px)`, info.minIn >= 150, info);
    await p.locator('.wg-panel input[type=text]').first().fill('Mein Gedanke');
    await sleep(400);
    ok('#2: Live-Vorschau im Widget', (await narrow.locator('.wg-t').innerText()).includes('Mein Gedanke'));
    await p.keyboard.press('Escape');
    ok('#2: Esc schliesst die Seitenleiste', !(await p.evaluate(() => !!document.querySelector('.wg-panel') || !!document.querySelector('.wg-editing'))));
    await sleep(3000);
    const body = JSON.parse((await p.api('GET', '/api/notes/' + MT.root)).body).blocks;
    ok('#2: Titel gespeichert', JSON.stringify(body).includes('Mein Gedanke'));
    // breiter Block (eigene Seite, eine Spalte): Panel bleibt im Block
    const W = await p.api('POST', '/api/notes', { title: 'Breites Widget' });
    await p.api('PATCH', '/api/notes/' + W.id, { format: 'blocks', body: JSON.stringify({ blocks: [{ type: 'widget', data: { kind: 'clock', v: 1, cfg: {} } }] }) });
    await p.open(W.id); await until(p, () => !!document.querySelector('.wg-blk .wg'));
    await p.locator('.wg-blk .wg').click(); await until(p, () => !!document.querySelector('.wg-panel'));
    ok('#2: breiter Block -> Panel im Block wie bisher', await p.evaluate(() => !!document.querySelector('.wg-blk .wg-panel:not(.wg-panel-float)')));
    await p.locator('.wg-done').click();
  }

  console.log(p.errs.length ? 'Seitenfehler: ' + p.errs.join(' | ') : 'keine Seitenfehler');
  ok('keine Seitenfehler', !p.errs.length, p.errs);
  await br.close();
  const bad = res.filter((x) => !x).length;
  console.log(bad ? `${bad} von ${res.length} FEHLGESCHLAGEN` : `ALLES GRUEN (${res.length})`);
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
