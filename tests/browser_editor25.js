// Saga 2.5 P6-1/P6-4 im Web: Inline-Farben, 6 Spalten (+ Dreierreihen bei 900 px), Mehrfachauswahl-Leiste (Loeschen mit
// Rueckgaengig, Duplizieren, Farbe, Umwandeln, Kopieren, Verschieben auf eine andere Seite), Mathe (Block + Inline, keine
// CSP-Verstoesse), Mermaid (Vorschau als Bild, Vorschaubild fuer die Apps, XSS-Versuche laufen nie), Vorschaubild veraltet.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> node tests/browser_editor25.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 10000) { const t = Date.now(); let v; while (Date.now() - t < ms) { v = await fn(); if (v) return v; await sleep(150); } return v; }
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused|TypeError|ReferenceError/.test(m.text())) errs.push(m.text().slice(0, 200)); });
  p.on('dialog', (d) => { errs.push('dialog: ' + d.message()); d.dismiss(); });
  await p.addInitScript(() => { window.__xss = 0; });
  await p.goto(BASE + '/notes');
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const P = (id, t) => ({ id, type: 'paragraph', data: { text: t } });
  const mk = async (title, blocks) => {
    const id = (await api('POST', '/api/notes', { title })).id;
    await api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks }), format: 'blocks' });
    return id;
  };
  const blocksOf = async (id) => JSON.parse((await api('GET', '/api/notes/' + id)).body).blocks;
  const open = async (id, sel) => { await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector(sel || '.ce-block'); await sleep(1500); };
  const saved = async (id, fn) => until(async () => fn(await blocksOf(id)), 12000);
  const made = [];

  // --- 1. Inline-Farbe -----------------------------------------------------------------------------------------------
  const f = await mk('Farben', [P('f1', 'Hallo schoene Welt'), P('f2', 'zweiter')]); made.push(f);
  await open(f, '.ce-block[data-id="f1"]');
  const selWord = async (bid, word) => p.evaluate(([bid, word]) => {
    const e = document.querySelector(`.ce-block[data-id="${bid}"] .ce-paragraph`), tw = document.createTreeWalker(e, NodeFilter.SHOW_TEXT);
    let tn = null; while (tw.nextNode()) if (tw.currentNode.nodeValue.includes(word)) { tn = tw.currentNode; break; }
    const i = tn.nodeValue.indexOf(word), r = document.createRange(); r.setStart(tn, i); r.setEnd(tn, i + word.length);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r); e.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  }, [bid, word]);
  await p.click('.ce-block[data-id="f1"] .ce-paragraph'); await selWord('f1', 'schoene');
  await p.waitForSelector('.ce-inline-tool.hk-color-tool', { state: 'visible', timeout: 5000 }).catch(() => null);
  const hasTool = await p.$('.ce-inline-tool.hk-color-tool');
  ok('Inline-Leiste hat das Farbwerkzeug', !!hasTool);
  if (hasTool) {
    await p.click('.ce-inline-tool.hk-color-tool');
    await p.waitForSelector('.hk-cpal');
    await p.click('.hk-cpal-b.hk-c-red');
  }
  let bl = await saved(f, (b) => /hk-c-red/.test(b[0].data.text) && b);
  ok(`Textfarbe gespeichert (${bl && bl[0].data.text})`, bl && bl[0].data.text === 'Hallo <span class="hk-c-red">schoene</span> Welt');
  await selWord('f1', 'schoene');
  await p.evaluate(() => HKEd25.applyColor(getSelection().getRangeAt(0), { c: 'blue', bg: true }));
  bl = await saved(f, (b) => /hk-bg-blue/.test(b[0].data.text) && b);
  ok(`Hintergrund dazu, span aussen (${bl && bl[0].data.text})`, bl && bl[0].data.text === 'Hallo <span class="hk-c-red"><mark class="hk-bg-blue">schoene</mark></span> Welt');
  await selWord('f1', 'schoene');
  await p.evaluate(() => HKEd25.applyColor(getSelection().getRangeAt(0), { c: null, bg: false }));
  bl = await saved(f, (b) => !/hk-c-red/.test(b[0].data.text) && b);
  ok(`Textfarbe entfernt, Hintergrund bleibt (${bl && bl[0].data.text})`, bl && bl[0].data.text === 'Hallo <mark class="hk-bg-blue">schoene</mark> Welt');
  // Teil einer gefaerbten Stelle umfaerben: vorne/hinten bleibt
  await api('PATCH', '/api/notes/' + f, { body: JSON.stringify({ blocks: [P('f1', '<span class="hk-c-green">abcdef</span>'), P('f2', 'x')] }), format: 'blocks' });
  await open(f, '.ce-block[data-id="f1"]');
  await p.click('.ce-block[data-id="f1"] .ce-paragraph');
  await p.evaluate(() => { const t = document.querySelector('.ce-block[data-id="f1"] span.hk-c-green').firstChild, r = document.createRange(); r.setStart(t, 2); r.setEnd(t, 4);
    const s = getSelection(); s.removeAllRanges(); s.addRange(r); HKEd25.applyColor(r, { c: 'red', bg: false }); });
  bl = await saved(f, (b) => /hk-c-red/.test(b[0].data.text) && b);
  ok(`Teil umgefaerbt (${bl && bl[0].data.text})`, bl && bl[0].data.text === '<span class="hk-c-green">ab</span><span class="hk-c-red">cd</span><span class="hk-c-green">ef</span>');
  ok('zuletzt benutzte Farbe gemerkt', JSON.parse(await p.evaluate(() => localStorage.getItem('hk-last-color'))).c === 'red');

  // --- 2. Sechs Spalten, Dreierreihen bei 900 px --------------------------------------------------------------------
  const c = await mk('Spalten', [{ id: 'k', type: 'columns', data: { cols: [{ blocks: [P('k1', 'eins')] }, { blocks: [P('k2', 'zwei')] }] } }]); made.push(c);
  await open(c, '.cols');
  await p.hover('.cols'); await p.click('.cols-ctl-b[aria-label="6 Spalten"]');
  bl = await saved(c, (b) => b[0].data.cols.length === 6 && b);
  ok('6 Spalten gespeichert', bl && bl[0].data.cols.length === 6);
  const cols = async () => p.evaluate(() => getComputedStyle(document.querySelector('.cols')).gridTemplateColumns.split(' ').length);
  ok('1280 px: 6 nebeneinander', (await cols()) === 6, await cols());
  await p.setViewportSize({ width: 900, height: 900 }); await sleep(300);
  ok('900 px: Dreierreihen', (await cols()) === 3, await cols());
  await p.setViewportSize({ width: 600, height: 900 }); await sleep(300);
  ok('600 px: untereinander', (await cols()) === 1, await cols());
  await p.setViewportSize({ width: 1280, height: 900 });

  // --- 3. Mehrfachauswahl ------------------------------------------------------------------------------------------
  const m = await mk('Auswahl', [P('a1', 'eins'), P('a2', 'zwei'), P('a3', 'drei'), P('a4', 'vier')]); made.push(m);
  const ZT = 'Ziel der Auswahl ' + Date.now(); const z = await mk(ZT, [P('z1', 'Ziel')]); made.push(z);
  await open(m, '.ce-block[data-id="a4"]');
  const select = async (ids) => { await p.evaluate((ids) => { ids.forEach((i) => document.querySelector(`.ce-block[data-id="${i}"]`).classList.add('ce-block--selected')); HKEd25.updateBar(); }, ids); await sleep(200); };
  await select(['a2', 'a3']);
  ok('Leiste erscheint ab 2 Bloecken', await p.isVisible('.hk-sel-bar'));
  ok('Anzahl angezeigt', /2/.test(await p.textContent('.hk-sel-n')));
  await p.click('.hk-sel-b[data-a="dup"]');
  bl = await saved(m, (b) => b.length === 6 && b);
  ok(`Duplizieren: Kopien hinter dem letzten markierten (${bl && bl.map((x) => x.data.text)})`, bl && bl.map((x) => x.data.text).join(',') === 'eins,zwei,drei,zwei,drei,vier');
  ok('Duplikate haben neue IDs', bl && new Set(bl.map((x) => x.id)).size === 6);
  await open(m, '.ce-block[data-id="a4"]');
  await select(['a2', 'a3']);
  await p.click('.hk-sel-b[data-a="color"]'); await p.waitForSelector('.menu .menu-item');
  await p.click('.menu .menu-item:has-text("Blau (Hintergrund)")');
  bl = await saved(m, (b) => b[1].tunes && b[1].tunes.hkstyle && b);
  ok('Farbe fuer alle markierten (tunes.hkstyle)', bl && JSON.stringify(bl[1].tunes.hkstyle) === '{"c":"blue","bg":true}' && JSON.stringify(bl[2].tunes.hkstyle) === '{"c":"blue","bg":true}');
  await open(m, '.ce-block[data-id="a4"]');
  await select(['a2', 'a3']);
  await p.click('.hk-sel-b[data-a="convert"]'); await p.waitForSelector('.menu .menu-item');
  await p.click('.menu .menu-item:has-text("Aufzählung")');
  bl = await saved(m, (b) => b.some((x) => x.type === 'list') && b);
  const li = bl && bl.find((x) => x.type === 'list');
  ok(`Umwandeln in Liste: eine Liste mit beiden Texten (${li && JSON.stringify(li.data.items.map((i) => i.content))})`, li && li.data.items.map((i) => i.content).join('|') === 'zwei|drei' && li.id === 'a2');
  await open(m, '.ce-block[data-id="a4"]');
  const ids0 = (await blocksOf(m)).map((x) => x.id);
  await select([ids0[1], ids0[2]]);
  await p.click('.hk-sel-b[data-a="delete"]');
  bl = await saved(m, (b) => b.length === ids0.length - 2 && b);
  ok('Loeschen: beide weg (ein Speichern)', !!bl);
  await p.click('.toast-action');
  bl = await saved(m, (b) => b.length === ids0.length && b);
  ok(`Rueckgaengig: wieder da, gleiche IDs (${bl && bl.map((x) => x.id)})`, bl && bl.map((x) => x.id).join() === ids0.join());
  // Kopieren: Zwischenablage-Formen
  await open(m, '.ce-block[data-id="a4"]');
  await select(['a1', 'a4']);
  await p.evaluate(() => { window.__clip = null; const orig = DataTransfer.prototype.setData;
    DataTransfer.prototype.setData = function (t, v) { (window.__clip = window.__clip || {})[t] = v; return orig.call(this, t, v); }; });
  await p.click('.hk-sel-b[data-a="copy"]');
  const clip = (await until(() => p.evaluate(() => window.__clip), 5000)) || {};
  ok(`Kopieren: Editor-JSON + Text (${clip['text/plain']})`, /"tool":"paragraph"/.test(clip['application/x-editor-js']) && clip['text/plain'] === 'eins\n\nvier');
  // Verschieben nach ... (P6-4) ueber die Leiste
  await open(m, '.ce-block[data-id="a4"]');
  await select(['a1', 'a4']);
  await p.click('.hk-sel-b[data-a="move"]'); await p.waitForSelector('.menu .menu-item');
  await p.fill('.menu input.menu-search', ZT); await sleep(150);
  await p.click(`.menu .menu-item:visible:has-text("${ZT}")`);
  const zb = await saved(z, (b) => b.length === 3 && b);
  ok(`Verschoben: Ziel hat die Bloecke am Ende (${zb && zb.map((x) => x.id)})`, zb && zb.map((x) => x.id).join() === 'z1,a1,a4');
  const mb = await saved(m, (b) => !b.some((x) => x.id === 'a1' || x.id === 'a4') && b);
  ok('Quelle ohne die Bloecke (Editor uebernimmt den Stand)', !!mb && await until(() => p.evaluate(() => !document.querySelector('.ce-block[data-id="a1"]'))));
  await sleep(300);
  ok('Hinweis mit Rueckgaengig und Oeffnen', await p.isVisible('.toast-action:has-text("Rückgängig")') && await p.isVisible('.toast-action:has-text("Öffnen")'));
  await p.click('.toast-action:has-text("Rückgängig")');
  const back = await saved(m, (b) => b.some((x) => x.id === 'a1') && b);
  ok(`Rueckgaengig: Bloecke zurueck an alter Stelle (${back && back.map((x) => x.id)})`, back && back[0].id === 'a1' && back.some((x) => x.id === 'a4'));
  ok('Ziel wieder ohne die Bloecke', (await blocksOf(z)).map((x) => x.id).join() === 'z1');

  // --- 4. Mathe -----------------------------------------------------------------------------------------------------
  const mt = await mk('Mathe', [{ id: 'mm', type: 'math', data: { tex: '\\frac{a}{b}' } }, P('mi', 'Inline <code class="hk-math">x^2</code> hier')]); made.push(mt);
  await open(mt, '.hk-mathblk');
  ok('Mathe-Block mit KaTeX gerendert', await until(() => p.evaluate(() => !!document.querySelector('.hk-math-view .katex .mfrac'))));
  const r1 = await saved(mt, (b) => b[0].data.render && b[0].data.render.png && b[0].data.render);
  ok(`Vorschaubild fuer die Apps hochgeladen (${JSON.stringify(r1)})`, r1 && /^\/files\/[0-9a-f]{24}$/.test(r1.png) && !r1.stale && r1.w > 0);
  const png = r1 && await p.evaluate(async (u) => { const r = await fetch(u); const b = new Uint8Array(await r.arrayBuffer()); return [r.status, b[1], b[2], b[3]]; }, r1.png);
  ok('Vorschaubild ist ein PNG', png && png[0] === 200 && String.fromCharCode(png[1], png[2], png[3]) === 'PNG');
  // Quelltext aendern -> neues Bild (anderer Hash)
  await p.click('.hk-math-view'); await p.fill('.hk-math-ta', '\\sqrt{x}');
  const r2 = await saved(mt, (b) => b[0].data.tex === '\\sqrt{x}' && b[0].data.render && b[0].data.render.png !== r1.png && b[0].data.render, 15000);
  ok('Quelltext geaendert -> neues Vorschaubild, nicht veraltet', r2 && !r2.stale);
  await p.evaluate(() => document.activeElement && document.activeElement.blur()); await sleep(500);
  await p.click('.ce-block[data-id="mi"] code.hk-math');
  ok('Inline-Mathe: Popover mit KaTeX', await until(() => p.evaluate(() => !!document.querySelector('.hk-mpop .katex'))),
     await p.evaluate(() => [!!document.querySelector('.hk-mpop'), (document.querySelector('.ce-block[data-id="mi"]') || {}).innerHTML]));
  await p.keyboard.press('Escape');
  // Server markiert veraltet, wenn ein anderer Client den Quelltext ohne Bild aendert
  const cur = await blocksOf(mt);
  cur[0].data.tex = 'y^3';
  await api('PATCH', '/api/notes/' + mt, { body: JSON.stringify({ blocks: cur }), format: 'blocks' });
  ok('Quelltext ohne Bild geaendert -> render.stale', (await blocksOf(mt))[0].data.render.stale === true);
  await open(mt, '.hk-mathblk');
  const r3 = await saved(mt, (b) => b[0].data.render && !b[0].data.render.stale && b[0].data.render, 15000);
  ok('Beim Oeffnen im Web neu erzeugt (nicht mehr veraltet)', !!r3);

  // --- 5. Mermaid + XSS-Versuche ------------------------------------------------------------------------------------
  const X = [
    'graph TD; A["<img src=x onerror=window.parent.__xss=1>"]-->B',
    'graph TD; A-->B; click A "javascript:window.parent.__xss=1"',
    'graph TD; A-->B; click A callback "x"',
    'graph TD; A[<a href="javascript:alert(1)">x</a>]-->B',
    '%%{init: {"securityLevel":"loose","htmlLabels":true}}%%\ngraph TD; A["<b onmouseover=alert(1)>x</b>"]-->B',
    'graph TD; A-->B; style A fill:#f9f,stroke:url(javascript:alert(1))',
    'graph TD; A-->B; classDef x fill:red</style><script>alert(1)</script>; class A x',
    '---\nconfig:\n  htmlLabels: true\n  securityLevel: loose\n  flowchart:\n    htmlLabels: true\n---\ngraph TD; A["<img src=x onerror=window.parent.__xss=1>"]-->B',
  ];
  const mm = await mk('Mermaid', [{ id: 'g0', type: 'code', data: { code: 'graph TD; Start-->Ende', language: 'mermaid' } }]
    .concat(X.map((x, i) => ({ id: 'x' + i, type: 'code', data: { code: x, language: 'mermaid', view: 'preview' } })))); made.push(mm);
  await open(mm, '.hk-code.hk-mermaid');
  ok('Mermaid-Vorschau als <img> (blob:)', await until(() => p.evaluate(() => { const i = document.querySelector('.ce-block[data-id="g0"] .hk-mmd img'); return i && i.src.startsWith('blob:') && i.naturalWidth > 0; }), 20000));
  await sleep(4000);
  const dom = await p.evaluate(() => ({
    svgInPage: document.querySelectorAll('#pg-editor svg[id^="hkm"], #pg-editor .hk-mmd svg').length,
    fo: document.querySelectorAll('#pg-editor foreignObject').length,
    frame: (() => { const fr = document.querySelector('iframe.hk-mmd-frame'); return fr && fr.getAttribute('sandbox'); })(),
    xss: window.__xss, imgs: document.querySelectorAll('#pg-editor .hk-mmd img').length,
  }));
  ok(`Kein Mermaid-SVG im Seiten-DOM (${dom.svgInPage}), kein foreignObject (${dom.fo})`, dom.svgInPage === 0 && dom.fo === 0);
  ok(`Rahmen abgeschottet (sandbox="${dom.frame}")`, dom.frame === 'allow-scripts');
  ok(`XSS-Versuche: nichts ausgefuehrt (window.__xss=${dom.xss})`, dom.xss === 0);
  // Klicks auf die Diagramme loesen nichts aus
  for (let i = 0; i < X.length; i++) { const im = await p.$(`.ce-block[data-id="x${i}"] .hk-mmd img`); if (im) await im.click({ force: true }).catch(() => {}); }
  await sleep(500);
  ok('Klick auf Diagramme: nichts ausgefuehrt', (await p.evaluate(() => window.__xss)) === 0 && p.url().startsWith(BASE));
  // die zurueckgegebenen SVGs selbst: keine Skripte/Ereignisse (Strict + htmlLabels:false)
  const svgs = await p.evaluate(async (X) => {
    const out = [];
    for (const s of X) { try { const r = await HKEd25.renderMermaid(s); URL.revokeObjectURL(r.url); out.push(r.svg); } catch (e) { out.push(''); } }
    return out;
  }, X);
  const bad = svgs.filter((t) => /<script|\son[a-z]+\s*=|javascript:|<foreignObject/i.test(t));
  ok(`SVG-Ausgabe ohne Skript/Ereignis/javascript:/foreignObject (${bad.length} von ${svgs.filter(Boolean).length})`, bad.length === 0, bad.map((t) => t.slice(0, 200)));
  const g0 = await saved(mm, (b) => b[0].data.render && b[0].data.render.png && b[0].data.render, 15000);
  ok('Mermaid: Vorschaubild hochgeladen', !!g0 && !g0.stale);
  // Ansicht umschalten wird gespeichert
  await p.hover('.ce-block[data-id="g0"] .hk-code'); await p.click('.ce-block[data-id="g0"] .hk-code-vb[data-v="split"]');
  ok('Ansicht "Geteilt" gespeichert', !!(await saved(mm, (b) => b[0].data.view === 'split')));

  ok(`Keine Fehler/CSP-Verstoesse (${errs.join(' / ')})`, errs.length === 0, errs);
  for (const id of made) await api('DELETE', '/api/notes/' + id);
  await br.close();
  console.log(`\nEDITOR25 ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
