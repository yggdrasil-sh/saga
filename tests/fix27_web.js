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

  // --- #4: Kommentar-Entwurf bleibt, wenn jemand anderes die Seite aendert ---------------------------------------------
  {
    const C = await p.api('POST', '/api/notes', { title: 'Kommentar-Entwurf' });
    await p.api('PATCH', '/api/notes/' + C.id, { format: 'blocks', body: JSON.stringify({ blocks: [{ type: 'paragraph', data: { text: 'Start' } }] }) });
    await p.open(C.id);
    await p.evaluate(() => window.HKComments.openAll()); await sleep(500);
    await p.locator('.cm-foot .cm-ta').fill('Entwurf zwei');
    // zweiter Tab desselben Kontos aendert die Seite
    const p2 = await page(CK); await p2.open(C.id);
    await p2.locator('.ce-paragraph').first().click(); await p2.keyboard.press('End'); await p2.keyboard.type(' geaendert');
    let seen = false;
    for (let i = 0; i < 30 && !seen; i++) { await sleep(300); seen = await p.evaluate(() => /geaendert/.test(document.querySelector('#pg-editor').textContent)); }
    await sleep(2500);
    ok('#4: fremde Aenderung ist angekommen', seen);
    const v = await p.evaluate(() => [...document.querySelectorAll('.cm-ta')].map((t) => t.value));
    ok('#4: Entwurf im Kommentarfeld erhalten', v.includes('Entwurf zwei'), v);
    // Antwort-Entwurf ebenso
    await p.locator('.cm-foot .cm-ta').fill('Erster Kommentar'); await p.locator('.cm-foot .cm-send').click(); await sleep(800);
    ok('#4: nach dem Senden ist das Feld leer', (await p.locator('.cm-foot .cm-ta').inputValue()) === '');
    await p.locator('.cm-reply, .cm-tbtn').filter({ hasText: 'Antworten' }).first().click();
    await p.locator('.cm-list .cm-ta, #pg-cm .cm-thread .cm-ta').first().fill('Antwort-Entwurf');
    await p2.keyboard.type(' nochmal');
    await sleep(5000);
    const v2 = await p.evaluate(() => [...document.querySelectorAll('.cm-ta')].map((t) => t.value));
    ok('#4: Antwort-Entwurf erhalten', v2.includes('Antwort-Entwurf'), v2);
    await p2.context().close();
  }

  // --- #5: Galerie-Dialog am Handy (390 px): Suchfeld voll breit; Suche ohne Umlaut-/Akzent-Unterschied ---------------
  {
    const m = await page(CK, { viewport: { width: 390, height: 800 }, isMobile: true, hasTouch: true });
    await m.open(AP.root);
    await m.evaluate(() => document.getElementById('pg-new').click()); await sleep(300);
    await m.evaluate(() => [...document.querySelectorAll('.menu-item')].find((x) => /Aus der Galerie/.test(x.textContent)).click());
    await until(m, () => document.querySelectorAll('#pg-modal .gal-tile').length > 0);
    const g = await m.evaluate(() => { const q = document.querySelector('#pg-modal .gal-q'), d = document.querySelector('#pg-modal .modal-body');
      return { q: Math.round(q.getBoundingClientRect().width), d: Math.round(d.clientWidth), sx: document.documentElement.scrollWidth <= innerWidth }; });
    await shot(m, '5-galerie-390');
    ok(`#5: Suchfeld am Handy breit (${g.q} von ${g.d} px)`, g.q >= g.d * 0.8 && g.q >= 250 && g.sx, g);
    const find = async (t) => { await m.fill('#pg-modal .gal-q', t); await sleep(200); return m.evaluate(() => [...document.querySelectorAll('#pg-modal .gal-tile')].map((x) => x.dataset.id)); };
    let r = await find('buch'); ok('#5: "buch" findet die Leseliste (Bücher)', r.includes('leseliste'), r);
    r = await find('BUECHER'); ok('#5: "BUECHER" findet die Leseliste', r.includes('leseliste'), r);
    r = await find('bücher'); ok('#5: "bücher" findet die Leseliste', r.includes('leseliste'), r);
    r = await find('zzzqqq'); ok('#5: Unsinn findet nichts', !r.length, r);
    ok('#5: keine Seitenfehler am Handy', !m.errs.length, m.errs);
    await m.context().close();
  }

  // --- #6: Eintrags-Vorlagen nicht im "+ Neue Seite"-Menue und nicht in der Relations-Auswahl ----------------------------
  {
    const all = await p.api('GET', '/api/notes'), byId = {}; all.forEach((x) => { byId[x.id] = x; });
    const rowTpl = all.filter((x) => x.template && (byId[x.parent_id] || {}).kind === 'database').map((x) => x.title);
    const pageTpl = await p.api('POST', '/api/notes', { title: 'Meine Seitenvorlage' });
    await p.api('PATCH', '/api/notes/' + pageTpl.id, { template: true });
    ok(`#6: Eintrags-Vorlagen vorhanden (${rowTpl.join(', ')})`, rowTpl.includes('Neue Aufgabe'));
    await p.open(AP.root);
    await p.click('#pg-new'); await sleep(300);
    const mi = await p.evaluate(() => [...document.querySelectorAll('.menu .menu-item')].map((x) => x.textContent.trim()));
    ok('#6: "+ Neue Seite" ohne Eintrags-Vorlagen', !mi.some((t) => rowTpl.some((r) => t.includes(r) && /eigene/.test(t))), mi);
    ok('#6: eigene Seitenvorlage weiter im Menue', mi.some((t) => t.includes('Meine Seitenvorlage')), mi);
    await p.keyboard.press('Escape');
    const proj = all.find((x) => x.title === 'Website überarbeiten' && !x.template);
    await p.open(proj.id);
    await p.click('button.val-btn[aria-label="Aufgaben"]'); await sleep(300);
    const opts = await p.evaluate(() => [...document.querySelectorAll('.menu .opt-item')].map((x) => x.textContent.replace('✓', '').trim()));
    ok(`#6: Relations-Auswahl ohne Eintrags-Vorlage (${opts.length} Eintraege)`, opts.length > 3 && !opts.some((t) => t.endsWith('Neue Aufgabe')), opts);
    await p.keyboard.press('Escape');
  }

  // --- #10: Zahlenformat (Prozent ganzzahlig, Euro mit 2 Nachkommastellen) und Fortschritt ueber 100 % ------------------
  {
    const D = (await p.api('POST', '/api/notes', { kind: 'database', title: 'Format-DB' })).id;
    const sc = (await p.api('GET', '/api/notes/' + D)).schema;
    sc.props.push({ id: 'eur', name: 'Betrag', type: 'number', fmt: 'euro' }, { id: 'pc', name: 'Anteil', type: 'number', fmt: 'percent' });
    sc.views = [{ id: 'tab', name: 'Tabelle', type: 'table' }];
    await p.api('PATCH', '/api/notes/' + D, { schema: sc });
    await p.api('POST', '/api/notes', { parent_id: D, title: 'A', props: { eur: 1000.5, pc: 41.04 } });
    await p.api('POST', '/api/notes', { parent_id: D, title: 'B', props: { eur: 430 } });
    const W = await p.api('POST', '/api/notes', { title: 'Format-Seite' });
    await p.api('PATCH', '/api/notes/' + W.id, { format: 'blocks', body: JSON.stringify({ blocks: [
      { id: 'wpr', type: 'widget', data: { v: 1, kind: 'progress', title: 'Budget', cfg: { src: 'db', db: D, view: '', mode: 'sum', prop: 'eur', goal: 1200 } } },
      { id: 'wme', type: 'widget', data: { v: 1, kind: 'metric', title: 'Summe', cfg: { db: D, view: '', agg: 'sum', prop: 'eur', fmt: 'eur' } } },
      { id: 'wpc', type: 'widget', data: { v: 1, kind: 'metric', title: 'Quote', cfg: { db: D, view: '', agg: 'sum', prop: 'pc', fmt: 'pct' } } },
      { id: 'dbb', type: 'database', data: { db: D, view: 'tab' } }] }) });
    await p.open(W.id);
    await until(p, () => document.querySelectorAll('.wg-v').length >= 3 && ![...document.querySelectorAll('.wg-v')].some((x) => x.textContent === '…'));
    const nb = (t) => t.replace(/\u00a0|\u202f/g, ' ').trim();
    const wv = (await p.evaluate(() => [...document.querySelectorAll('.wg')].map((x) => [x.querySelector('.wg-v') && x.querySelector('.wg-v').textContent, (x.querySelector('.wg-sub') || {}).textContent || '', x.querySelector('.wg-fill') ? x.querySelector('.wg-fill').style.width : '']))).map((x) => x.map(nb));
    ok(`#10: Fortschritt ueber 100 % echt (${wv[0][0]}), Balken voll (${wv[0][2]})`, wv[0][0] === '119 %' && parseFloat(wv[0][2]) === 100, wv[0]);
    ok(`#10: Fortschritt in Euro (${wv[0][1]})`, wv[0][1] === '1.430,50 € / 1.200,00 €', wv[0]);
    ok(`#10: Kennzahl Euro mit 2 Nachkommastellen (${wv[1][0]})`, wv[1][0] === '1.430,50 €', wv[1]);
    ok(`#10: Kennzahl Prozent ganzzahlig (${wv[2][0]})`, wv[2][0] === '41 %', wv[2]);
    await until(p, () => !!document.querySelector('.embed-db .db-row'));
    const cells = (await p.evaluate(() => [...document.querySelectorAll('.embed-db .nfmt-t')].map((x) => x.textContent))).map(nb);
    ok(`#10: Tabelle Prozent ganzzahlig, Euro ,00 (${cells.join(' | ')})`, cells.includes('41 %') && cells.includes('1.000,50 €') && cells.includes('430,00 €') && !cells.some((c) => /41,04/.test(c)), cells);
    // englisch: Schreibweise der Sprache
    const e = await page(CK, { locale: 'en-US' });
    await e.context().addCookies([{ name: 'saga_lang', value: 'en', domain: new URL(BASE).hostname, path: '/' }]);
    await e.open(W.id);
    await until(e, () => document.querySelectorAll('.wg-v').length >= 3 && ![...document.querySelectorAll('.wg-v')].some((x) => x.textContent === '…'));
    const ev = (await e.evaluate(() => [...document.querySelectorAll('.wg-v, .wg-sub')].map((x) => x.textContent))).map(nb);
    ok(`#10: englisch (${ev.join(' | ')})`, ev.includes('119%') && ev.includes('€1,430.50 / €1,200.00') && ev.includes('€1,430.50') && ev.includes('41%'), ev);
    await e.context().close();
  }

  console.log(p.errs.length ? 'Seitenfehler: ' + p.errs.join(' | ') : 'keine Seitenfehler');
  ok('keine Seitenfehler', !p.errs.length, p.errs);
  await br.close();
  const bad = res.filter((x) => !x).length;
  console.log(bad ? `${bad} von ${res.length} FEHLGESCHLAGEN` : `ALLES GRUEN (${res.length})`);
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
