// Notion-Gefuehl im Web (2.5, Quick Wins): leere Seite (Griffe auf der ersten Zeile, kurzer Platzhalter), Block-Menue "/"
// ohne Suchfeld (Filtern im Text, unter der Zeile), stiller Speicherstatus, Schreibflaeche 16 px / Titel 40 px.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Sitzungs-Cookie hk-test-a> node tests/notion_gefuehl_web.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const P = (t) => ({ type: 'paragraph', data: { text: t } });
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const errs = [];
  for (const scheme of ['light', 'dark']) {
    const ctx = await br.newContext({ viewport: { width: 1440, height: 900 }, locale: 'de-DE', colorScheme: scheme });
    await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.on('pageerror', (e) => errs.push(e.message));
    await p.goto(BASE + '/notes');
    const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
      const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
      return r.json().catch(() => ({}));
    }, [m, url, b]);
    const full = (await api('POST', '/api/notes', { title: 'NG voll' })).id;
    await api('PATCH', '/api/notes/' + full, { body: JSON.stringify({ blocks: [P('eins'), P('zwei'), P('drei'), P('vier'), P('fünf'), P('sechs')] }), format: 'blocks' });
    const empty = (await api('POST', '/api/notes', { title: '' })).id;
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + full); await p.waitForSelector('#pg-editor .ce-paragraph'); await sleep(800);
    const tag = scheme;
    // Schreibflaeche
    const ty = await p.evaluate(() => ({ body: parseFloat(getComputedStyle(document.querySelector('#pg-editor .ce-paragraph')).fontSize),
      title: parseFloat(getComputedStyle(document.getElementById('pg-title')).fontSize) }));
    ok(`${tag}: Text 16 px, Titel 40 px (${ty.body}/${ty.title})`, ty.body === 16 && ty.title === 40, ty);
    // Speicherstatus still beim Tippen und nach dem Speichern
    await p.locator('#pg-editor .ce-paragraph').last().click(); await p.keyboard.press('End'); await p.keyboard.type(' x');
    await sleep(200);
    const op = () => p.evaluate(() => { const s = document.getElementById('pg-state'); return { t: s.textContent, o: getComputedStyle(s).opacity }; });
    let st = await op(); ok(`${tag}: "Ungespeichert" unsichtbar (${st.t})`, st.o === '0', st);
    await sleep(2200); st = await op(); ok(`${tag}: "Gespeichert" unsichtbar (${st.t})`, /Gespeichert/.test(st.t) && st.o === '0', st);
    // Offline-Hinweis sichtbar
    await ctx.setOffline(true); await sleep(200); st = await op();
    ok(`${tag}: offline sichtbar (${st.t})`, /Offline/.test(st.t) && st.o !== '0', st);
    await p.keyboard.type(' off'); await sleep(1800);                 // offline getippt: Speichern scheitert
    st = await op(); ok(`${tag}: offline getippt - Hinweis bleibt (${st.t})`, /Offline/.test(st.t) && st.o !== '0', st);
    await ctx.setOffline(false); await sleep(2000);
    const back = await api('GET', '/api/notes/' + full);
    ok(`${tag}: wieder online - Offline-Text gespeichert`, /sechs x off/.test(back.body || ''), (back.body || '').slice(-120));
    // Block-Menue: kein Suchfeld, Filtern im Text, unter der Zeile
    await p.keyboard.press('Enter'); await p.keyboard.type('/'); await sleep(500);
    const m = await p.evaluate(() => { const pop = document.querySelector('.ce-popover--opened'), para = document.activeElement;
      const c = pop && pop.querySelector(':scope > .ce-popover__container'), s = pop && pop.querySelector('.ce-popover__search');
      return pop && { inl: pop.classList.contains('pg-slash-inline'), search: s ? s.getClientRects().length : 0, inPara: para.classList.contains('ce-paragraph'),
        top: Math.round(c.getBoundingClientRect().top), line: Math.round(para.getBoundingClientRect().bottom) }; });
    ok(`${tag}: Menue ohne Suchfeld, Cursor im Text`, m && m.inl && !m.search && m.inPara, m);
    ok(`${tag}: Menue unter der Zeile (${m && m.top} >= ${m && m.line})`, m && m.top >= m.line, m);
    await p.keyboard.type('zit'); await sleep(300);
    const vis = await p.evaluate(() => [...document.querySelectorAll('.ce-popover--opened .ce-popover-item')].filter((x) => x.getClientRects().length).map((x) => x.textContent.trim()));
    ok(`${tag}: Weitertippen filtert (${vis.join(', ')})`, vis.length >= 1 && vis.every((v) => /zit/i.test(v)), vis);
    await p.keyboard.press('Enter'); await sleep(400);
    ok(`${tag}: Enter macht ein Zitat`, await p.evaluate(() => !!document.activeElement.closest('.cdx-quote') && !document.querySelector('.ce-popover--opened')));
    await p.locator('#pg-editor .ce-paragraph', { hasText: 'sechs' }).click(); await p.keyboard.press('End'); await p.keyboard.press('Enter');
    await p.keyboard.type('/x'); await sleep(300);
    const x1 = await p.evaluate(() => !!document.querySelector('.ce-popover--opened.pg-slash-inline'));
    await p.keyboard.press('Backspace'); await p.keyboard.press('Backspace'); await sleep(300);
    ok(`${tag}: "/" geloescht -> Menue zu`, x1 && await p.evaluate(() => !document.querySelector('.ce-popover--opened')), x1);
    await p.keyboard.type('/'); await sleep(300); await p.keyboard.press('Escape'); await sleep(300);
    ok(`${tag}: Esc schliesst und nimmt "/" weg`, await p.evaluate(() => !document.querySelector('.ce-popover--opened') && document.activeElement.textContent === ''));
    // Esc bei "/…" mit Leerzeichen (ein Satz, kein Befehl): Menue zu, Text bleibt stehen
    await p.keyboard.type('/x y z'); await sleep(300); await p.keyboard.press('Escape'); await sleep(300);
    ok(`${tag}: Esc bei "/x y z" laesst den Text stehen`, await p.evaluate(() => !document.querySelector('.ce-popover--opened') && document.activeElement.textContent === '/x y z'));
    await sleep(1500);
    // Leere Seite nach Wechsel aus einer laengeren Seite: Werkzeugleiste nicht unten
    await p.locator('#pg-editor .ce-paragraph', { hasText: 'sechs' }).click(); await sleep(200);
    await p.locator('#pg-tree .pg-row, #pg-tree li', { hasText: 'Unbenannt' }).last().click(); await sleep(1500);
    const lp = await p.evaluate(() => { const t = document.querySelector('#pg-editor .ce-toolbar'), b = document.querySelector('#pg-editor .ce-paragraph');
      return { open: t.classList.contains('ce-toolbar--opened'), top: t.style.top, ph: b.dataset.placeholderActive || b.dataset.placeholder || '' }; });
    ok(`${tag}: leere Seite - Werkzeugleiste der vorigen Seite zu (${lp.top})`, !lp.open || lp.top === '0px', lp);
    ok(`${tag}: kurzer Platzhalter (${lp.ph})`, lp.ph === 'Tippe „/“ für Befehle', lp);
    const eb = await p.locator('#pg-editor .ce-paragraph').first().boundingBox();
    await p.mouse.move(eb.x + 30, eb.y + eb.height / 2); await sleep(300);
    const g = await p.evaluate(() => { const pl = document.querySelector('#pg-editor .ce-toolbar--opened .ce-toolbar__plus'), b = document.querySelector('#pg-editor .ce-paragraph');
      if (!pl) return null; const a = pl.getBoundingClientRect(), c = b.getBoundingClientRect(); return { plus: Math.round(a.top + a.height / 2), line: Math.round(c.top + c.height / 2) }; });
    ok(`${tag}: "+" auf Hoehe der ersten Zeile`, g && Math.abs(g.plus - g.line) <= 12, g);
    await api('DELETE', '/api/notes/' + full); await api('DELETE', '/api/notes/' + empty);
    await ctx.close();
  }
  ok('Keine Seitenfehler', !errs.length, errs);
  await br.close();
  console.log('NOTION-GEFUEHL-WEB ' + res.filter(Boolean).length + '/' + res.length + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
