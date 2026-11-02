// Notion-Gefuehl im Web (2.5, Quick Wins): leere Seite (Griffe auf der ersten Zeile, kurzer Platzhalter), Block-Menue "/"
// ohne Suchfeld (Filtern im Text, unter der Zeile), stiller Speicherstatus, Schreibflaeche 16 px / Titel 40 px.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Sitzungs-Cookie hk-test-a> node tests/notion_gefuehl_web.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');            // Bloecke ueber data-block-* und HKPages.k.doc (T1)
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
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + full); await p.waitForSelector(D.ofType('paragraph')); await sleep(800);
    const tag = scheme;
    // Schreibflaeche
    const ty = await p.evaluate((PS) => ({ body: parseFloat(getComputedStyle(document.querySelector(PS)).fontSize),
      title: parseFloat(getComputedStyle(document.getElementById('pg-title')).fontSize) }), D.textOf('paragraph'));
    ok(`${tag}: Text 16 px, Titel 40 px (${ty.body}/${ty.title})`, ty.body === 16 && ty.title === 40, ty);
    // Speicherstatus still beim Tippen und nach dem Speichern
    await p.locator(D.textOf('paragraph')).last().click(); await p.keyboard.press('End'); await p.keyboard.type(' x');
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
    // Tiptap: ein contenteditable fuer das ganze Dokument (activeElement = Editor) -> Block der Schreibmarke ueber die
    // Fassade; Menue (.pm-pop) ist selbst der Behaelter und hat nie ein Suchfeld
    const m = await p.evaluate(([U, TT]) => { const pop = document.querySelector(U.popover);
      const cur = window.HKPages && HKPages.k.doc && HKPages.k.doc.current(), blk = TT ? cur && cur.el : document.activeElement.closest('[data-block-id]');
      const para = TT ? blk : document.activeElement;
      const c = pop && (pop.matches(U.popoverContainer) ? pop : pop.querySelector(':scope > ' + U.popoverContainer)), s = pop && pop.querySelector(U.search);
      return pop && c && para && { inl: TT || pop.classList.contains('pg-slash-inline'), search: s ? s.getClientRects().length : 0, inPara: !!blk && blk.getAttribute('data-block-type') === 'paragraph',
        top: Math.round(c.getBoundingClientRect().top), line: Math.round(para.getBoundingClientRect().bottom) }; }, [D.UI, D.EDITOR === 'tiptap']);
    ok(`${tag}: Menue ohne Suchfeld, Cursor im Text`, m && m.inl && !m.search && m.inPara, m);
    ok(`${tag}: Menue unter der Zeile (${m && m.top} >= ${m && m.line})`, m && m.top >= m.line, m);
    await p.keyboard.type('zit'); await sleep(300);
    const vis = await p.evaluate((s) => [...document.querySelectorAll(s)].filter((x) => x.getClientRects().length).map((x) => x.textContent.trim()), `${D.UI.popover} ${D.UI.item}`);
    ok(`${tag}: Weitertippen filtert (${vis.join(', ')})`, vis.length >= 1 && vis.every((v) => /zit/i.test(v)), vis);
    await p.keyboard.press('Enter'); await sleep(400);
    ok(`${tag}: Enter macht ein Zitat`, await p.evaluate((U) => { const c = HKPages.k.doc.current();
      return !!(c ? c.type === 'quote' : document.activeElement.closest('[data-block-type="quote"]')) && !document.querySelector(U.popover); }, D.UI));
    await p.locator(D.textOf('paragraph'), { hasText: 'sechs' }).click(); await p.keyboard.press('End'); await p.keyboard.press('Enter');
    await p.keyboard.type('/x'); await sleep(300);
    const x1 = await p.evaluate(([U, TT]) => !!document.querySelector(U.popover + (TT ? '' : '.pg-slash-inline')), [D.UI, D.EDITOR === 'tiptap']);
    await p.keyboard.press('Backspace'); await p.keyboard.press('Backspace'); await sleep(300);
    ok(`${tag}: "/" geloescht -> Menue zu`, x1 && await p.evaluate((U) => !document.querySelector(U.popover), D.UI), x1);
    await p.keyboard.type('/'); await sleep(300); await p.keyboard.press('Escape'); await sleep(300);
    // Text des Blocks mit der Schreibmarke (Fassade; Editor.js: das Textfeld selbst)
    const curTxt = () => p.evaluate(() => { const c = HKPages.k.doc.current(); return c ? c.el.textContent : document.activeElement.textContent; });
    // Tiptap (E1, browser_tiptap_kern "Slash: Esc laesst ... stehen"): Esc schliesst nur, "/" bleibt Text
    const escWant = D.EDITOR === 'tiptap' ? '/' : '';
    ok(`${tag}: Esc schliesst und nimmt "/" weg` + (escWant ? ' (Tiptap: "/" bleibt)' : ''), await p.evaluate((U) => !document.querySelector(U.popover), D.UI) && (await curTxt()) === escWant, await curTxt());
    if (escWant) { await p.keyboard.press('Backspace'); await sleep(100); }
    // Esc bei "/…" mit Leerzeichen (ein Satz, kein Befehl): Menue zu, Text bleibt stehen
    await p.keyboard.type('/x y z'); await sleep(300); await p.keyboard.press('Escape'); await sleep(300);
    ok(`${tag}: Esc bei "/x y z" laesst den Text stehen`, await p.evaluate((U) => !document.querySelector(U.popover), D.UI) && (await curTxt()) === '/x y z', await curTxt());
    await sleep(1500);
    // Leere Seite nach Wechsel aus einer laengeren Seite: Werkzeugleiste nicht unten
    await p.locator(D.textOf('paragraph'), { hasText: 'sechs' }).click(); await sleep(200);
    await p.locator('#pg-tree .pg-row, #pg-tree li', { hasText: 'Unbenannt' }).last().click(); await sleep(1500);
    // Platzhalter: Editor.js data-placeholder(-active) am Textfeld, Tiptap data-ph (Dekoration) am Block oder darin
    const lp = await p.evaluate(([U, PS]) => { const t = document.querySelector(U.toolbar), b = document.querySelector(PS);
      const phEl = b && (b.closest('[data-ph]') || b.querySelector('[data-ph]'));
      return { open: !!t && t.classList.contains(U.toolbarOpenClass), top: t ? t.style.top : '0px',
        ph: (b && (b.dataset.placeholderActive || b.dataset.placeholder)) || (phEl && phEl.dataset.ph) || '' }; }, [D.UI, D.textOf('paragraph')]);
    ok(`${tag}: leere Seite - Werkzeugleiste der vorigen Seite zu (${lp.top})`, !lp.open || lp.top === '0px', lp);
    // Editor.js: "Tippe „/“ für Befehle" (pages.js), Tiptap: eigener kurzer Text der Schreibmarken-Zeile (pm-editor.js phText)
    ok(`${tag}: kurzer Platzhalter (${lp.ph})`, lp.ph === (D.EDITOR === 'tiptap' ? 'Schreib etwas oder tippe „/“ …' : 'Tippe „/“ für Befehle'), lp);
    const eb = await p.locator(D.textOf('paragraph')).first().boundingBox();
    await p.mouse.move(eb.x + 30, eb.y + eb.height / 2); await sleep(300);
    const g = await p.evaluate(([U, PS]) => { const pl = document.querySelector(U.plusBtn), b = document.querySelector(PS);
      if (!pl) return null; const a = pl.getBoundingClientRect(), c = b.getBoundingClientRect(); return { plus: Math.round(a.top + a.height / 2), line: Math.round(c.top + c.height / 2) }; }, [D.UI, D.textOf('paragraph')]);
    ok(`${tag}: "+" auf Hoehe der ersten Zeile`, g && Math.abs(g.plus - g.line) <= 12, g);
    await api('DELETE', '/api/notes/' + full); await api('DELETE', '/api/notes/' + empty);
    await ctx.close();
  }
  ok('Keine Seitenfehler', !errs.length, errs);
  await br.close();
  console.log('NOTION-GEFUEHL-WEB ' + res.filter(Boolean).length + '/' + res.length + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
