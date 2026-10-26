// Seite gestalten (2.5): Spaltenbreite ziehen (G1), Bild skalieren (G2), Farbe ueber das Block-Menue ⋮⋮ (G3/G5),
// Schrift umschalten (G9), Toggle-Ueberschrift im Inhaltsverzeichnis und Umwandeln (G10). Alte Seiten bleiben unveraendert.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Sitzungs-Cookie hk-test-a> [CK2=<Cookie hk-test-b>] node tests/gestalten.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused/.test(m.text())) errs.push(m.text()); });
  await p.goto(BASE + '/notes');
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const body = async (id) => JSON.parse((await api('GET', '/api/notes/' + id)).body || '{"blocks":[]}').blocks;
  // eigenes Bild hochladen (Seiten nehmen nur Saga-Bilder)
  const img = await p.evaluate(async () => {
    const c = document.createElement('canvas'); c.width = 600; c.height = 300; const g = c.getContext('2d'); g.fillStyle = '#2a7'; g.fillRect(0, 0, 600, 300);
    const blob = await new Promise((r) => c.toBlob(r, 'image/png')); const fd = new FormData(); fd.append('image', blob, 'b.png');
    const r = await fetch('/api/files', { method: 'POST', headers: { 'X-Saga': '1' }, body: fd }); return (await r.json()).file.url;
  });
  const P = (t) => ({ type: 'paragraph', data: { text: t } });
  const id = (await api('POST', '/api/notes', { title: 'Gestalten Web' })).id;
  const start = { blocks: [
    { type: 'header', data: { text: 'Kapitel A', level: 2 } },
    { type: 'paragraph', data: { text: 'Farbiger Absatz' } },
    { type: 'columns', data: { cols: [{ blocks: [P('Links')] }, { blocks: [P('Rechts')] }] } },
    { type: 'image', data: { file: { url: img }, caption: 'Bild', zukunft: { a: 1 } } },
    { type: 'toggle', data: { title: 'Ausklapp B', open: true, level: 2, blocks: [P('innen')] } },
    { type: 'header', data: { text: 'Kapitel C', level: 3 } },
    { type: 'paragraph', data: { text: 'Ende' } }] };
  await api('PATCH', '/api/notes/' + id, { body: JSON.stringify(start), format: 'blocks' });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
  await p.waitForSelector('.cols .col-rs'); await sleep(1200);

  // --- G10: Toggle-Ueberschrift ist eine Ueberschrift (Groesse, Inhaltsverzeichnis) ---------------------------
  const tglSize = await p.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.tgl-h2 .tgl-title')).fontSize));
  const pSize = await p.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.ce-paragraph')).fontSize));
  ok(`G10: Titel in Ueberschriftengroesse (${tglSize} > ${pSize})`, tglSize > pSize * 1.15);
  await sleep(500);
  const toc = await p.evaluate(() => [...document.querySelectorAll('.hk-toc .hk-toc-t')].map((x) => x.textContent));
  ok(`G10: Toggle-Ueberschrift im Inhaltsverzeichnis an ihrer Stelle (${toc.join(' | ')})`, toc.join('|') === 'Kapitel A|Ausklapp B|Kapitel C');

  // --- G1: Spaltenbreite ziehen ----------------------------------------------------------------------------------
  const h = await p.locator('.cols > .col-rs').first().boundingBox();
  const box = await p.locator('.cols').first().boundingBox();
  await p.mouse.move(h.x + h.width / 2, h.y + 40); await p.mouse.down();
  await p.mouse.move(box.x + box.width * 0.7, h.y + 40, { steps: 8 }); await p.mouse.up();
  await sleep(300);
  const tpl = await p.evaluate(() => getComputedStyle(document.querySelector('.cols')).gridTemplateColumns);
  const cw = await p.evaluate(() => [...document.querySelectorAll('.cols > .col')].map((c) => c.getBoundingClientRect().width));
  ok(`G1: Spalten nach dem Ziehen ungleich (${cw.map(Math.round)})`, cw[0] > cw[1] * 1.8, tpl);
  await sleep(3500);
  let bl = await body(id);
  let ws = bl[2].data.cols.map((c) => c.w);
  ok(`G1: Gewichte gespeichert, 5-%-Raster (${ws})`, ws[0] >= 0.65 && ws[0] <= 0.75 && Math.abs(ws[0] + ws[1] - 1) < 0.011 && Math.abs(ws[0] * 20 - Math.round(ws[0] * 20)) < 0.01, ws);
  await p.locator('.cols > .col-rs').first().dblclick(); await sleep(3500);
  bl = await body(id);
  ok('G1: Doppelklick setzt beide gleich (w entfernt)', bl[2].data.cols.every((c) => c.w === undefined), bl[2].data.cols);

  // --- G2: Bild skalieren --------------------------------------------------------------------------------------
  const im = p.locator('.image-tool__image').first();
  await im.scrollIntoViewIfNeeded(); await im.hover(); await sleep(200);
  const rs = await p.locator('.image-tool__image .hk-rs-r').first().boundingBox();
  const ib = await im.boundingBox();
  const ed = await p.locator('#pg-editor .codex-editor').first().boundingBox();
  await p.mouse.move(rs.x + rs.width / 2, rs.y + rs.height / 2); await p.mouse.down();
  await p.mouse.move(ed.x + ed.width * 0.75, rs.y + rs.height / 2, { steps: 8 }); await p.mouse.up();
  await sleep(300);
  const ib2 = await im.boundingBox();
  ok(`G2: Bild live schmaler (${Math.round(ib.width)} -> ${Math.round(ib2.width)})`, ib2.width < ib.width * 0.75);
  await sleep(3500);
  bl = await body(id);
  ok(`G2: Breite gespeichert (${bl[3].data.width} %), unbekanntes Feld bleibt`, bl[3].data.width >= 35 && bl[3].data.width <= 65 && bl[3].data.zukunft && bl[3].data.zukunft.a === 1, bl[3].data);

  // --- G3/G5: Farbe ueber das Block-Menue ⋮⋮ ---------------------------------------------------------------------
  const para = p.locator('.ce-paragraph', { hasText: 'Farbiger Absatz' }).first();
  await para.click(); await para.hover(); await sleep(250);
  await p.locator('.ce-toolbar__settings-btn:visible').first().click(); await sleep(300);
  const items = await p.evaluate(() => [...document.querySelectorAll('.ce-settings .ce-popover--opened > .ce-popover__container > .ce-popover__items > .ce-popover-item .ce-popover-item__title, .ce-popover--opened .ce-popover__items > .ce-popover-item .ce-popover-item__title')].map((x) => x.textContent.trim()));
  const iConv = items.indexOf('Umwandeln in'), iCol = items.indexOf('Farbe'), iDup = items.indexOf('Duplizieren'), iLink = items.indexOf('Link zum Block kopieren'), iCom = items.indexOf('Kommentieren');
  ok(`G5: Reihenfolge Umwandeln, Farbe, Duplizieren, Link, Kommentieren (${items.slice(0, 9).join(', ')})`,
    iConv >= 0 && iConv < iCol && iCol < iDup && iDup < iLink && iLink < iCom, items);
  await p.locator('.ce-popover--opened .ce-popover-item:has(.ce-popover-item__title:text-is("Farbe"))').first().click(); await sleep(250);
  await p.locator('.ce-popover--opened .ce-popover-item:has(.ce-popover-item__title:text-is("Blau (Hintergrund)"))').first().click(); await sleep(300);
  const cls = await para.evaluate((n) => n.closest('.ce-block__content').className);
  ok(`G3: Hintergrund sofort sichtbar (${cls})`, /hk-bg-blue/.test(cls));
  await sleep(3500);
  bl = await body(id);
  ok('G3: Farbe als tunes.hkstyle gespeichert', JSON.stringify(bl[1].tunes) === JSON.stringify({ hkstyle: { c: 'blue', bg: true } }), bl[1]);

  // Slash-Kuerzel "/rot" in einem neuen Absatz
  const end = p.locator('.ce-paragraph', { hasText: 'Ende' }).first();
  await end.click(); await p.keyboard.press('End'); await p.keyboard.press('Enter'); await sleep(200);
  await p.keyboard.type('/rot', { delay: 40 }); await sleep(500); await p.keyboard.press('Enter'); await sleep(300);
  await p.keyboard.type('rote Zeile'); await sleep(3800);
  bl = await body(id);
  const red = bl.find((b) => b.data && b.data.text === 'rote Zeile');
  ok('G3: "/rot" faerbt den Block rot', red && red.tunes && red.tunes.hkstyle && red.tunes.hkstyle.c === 'red' && !red.tunes.hkstyle.bg, red);

  // Duplizieren behaelt die Farbe
  await para.click(); await p.keyboard.press(process.platform === 'darwin' ? 'Meta+d' : 'Control+d'); await sleep(3800);
  bl = await body(id);
  ok('G5: Cmd/Strg+D dupliziert samt Farbe', bl.filter((b) => b.data && b.data.text === 'Farbiger Absatz' && b.tunes && b.tunes.hkstyle && b.tunes.hkstyle.c === 'blue').length === 2);

  // --- G10: Umwandeln Ueberschrift -> Toggle-Ueberschrift behaelt den Text ----------------------------------------
  const hC = p.locator('.ce-header', { hasText: 'Kapitel C' }).first();
  await hC.click(); await p.keyboard.press(process.platform === 'darwin' ? 'Meta+/' : 'Control+/'); await sleep(300);
  await p.keyboard.type('Toggle-Überschrift 1'); await sleep(150); await p.keyboard.press('Enter'); await sleep(3800);
  bl = await body(id);
  const t1 = bl.find((b) => b.type === 'toggle' && b.data.title === 'Kapitel C');
  ok('G10: Ueberschrift -> Toggle-Ueberschrift 1, Text bleibt', t1 && t1.data.level === 1, bl.map((b) => b.type + ':' + (b.data.level || '')));

  // --- G9: Schrift umschalten -----------------------------------------------------------------------------------
  await p.click('#pg-more'); await sleep(200);
  await p.locator('.menu-item:has(.mi-t:text-is("Schrift"))').first().click(); await sleep(200);
  await p.locator('.menu-sub .menu-item:has(.mi-t:text-is("Serif")), .menu .menu-item:has(.mi-t:text-is("Serif"))').last().click(); await sleep(800);
  const ff = await p.evaluate(() => getComputedStyle(document.querySelector('.page-body .ce-paragraph')).fontFamily);
  const tf = await p.evaluate(() => getComputedStyle(document.getElementById('pg-title')).fontFamily);
  ok(`G9: Serif auf Inhalt und Titel (${ff.slice(0, 30)})`, /serif/i.test(ff) && /Georgia|serif/.test(tf) && !/sans-serif/.test(ff));
  const cf = await p.evaluate(() => { const c = document.querySelector('.page-body .inline-code, .page-body .hk-code-ta'); return c ? getComputedStyle(c).fontFamily : 'monospace'; });
  ok('G9: Code bleibt Mono', /mono|Menlo|Consolas/i.test(cf), cf);
  await p.click('#pg-more'); await sleep(200);
  await p.locator('.menu-item:has(.mi-t:text-is("Kleiner Text"))').first().click(); await sleep(800);
  let n = await api('GET', '/api/notes/' + id);
  ok(`G9: gespeichert font=${n.font}, small=${n.small}`, n.font === 'serif' && n.small === true);
  const fs = await p.evaluate(() => parseFloat(getComputedStyle(document.querySelector('.page-body .ce-paragraph')).fontSize));
  ok(`G9: kleiner Text (${fs} < ${pSize})`, fs < pSize * 0.95);
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector('.page-body .ce-paragraph'); await sleep(800);
  ok('G9: nach Neuladen weiter Serif + klein', await p.evaluate(() => { const g = document.getElementById('page'); return g.classList.contains('pf-serif') && g.classList.contains('p-small'); }));

  // --- Alte Seite ohne neue Felder: Speichern aendert nichts an der Struktur -----------------------------------
  const old = (await api('POST', '/api/notes', { title: 'Alt' })).id;
  const oldBlocks = [{ type: 'columns', data: { cols: [{ blocks: [P('a')] }, { blocks: [P('b')] }] } }, { type: 'image', data: { file: { url: img }, caption: '', withBorder: false, withBackground: false, stretched: false } },
    { type: 'toggle', data: { title: 'T', open: true, blocks: [P('x')] } }, P('Text')];
  await api('PATCH', '/api/notes/' + old, { body: JSON.stringify({ blocks: oldBlocks }), format: 'blocks' });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + old); await p.waitForSelector('.cols'); await sleep(1000);
  await p.locator('.ce-paragraph', { hasText: 'Text' }).last().click(); await p.keyboard.press('End'); await p.keyboard.type('!'); await sleep(3800);
  bl = await body(old);
  const sorted = (v) => (Array.isArray(v) ? v.map(sorted) : v && typeof v === 'object' ? Object.keys(v).sort().reduce((o, k) => { o[k] = sorted(v[k]); return o; }, {}) : v);
  const strip = (l) => JSON.stringify(sorted(l.map((b) => ({ type: b.type, tunes: b.tunes, data: b.type === 'paragraph' ? undefined : b.data }))), (k, v) => (k === 'id' ? undefined : v));
  const want = strip(oldBlocks.map((b) => (b.type === 'columns' ? { type: 'columns', data: { cols: b.data.cols.map((c) => ({ blocks: c.blocks.map((x) => ({ type: x.type, data: x.data })) })) } } : b)));
  const got = strip(bl.map((b) => (b.type === 'columns' ? { type: 'columns', data: { cols: b.data.cols.map((c) => ({ blocks: c.blocks.map((x) => ({ type: x.type, data: x.data })) })) } }
    : b.type === 'toggle' ? { type: 'toggle', data: { title: b.data.title, open: b.data.open, blocks: b.data.blocks.map((x) => ({ type: x.type, data: x.data })) } } : b)));
  ok('Alte Seite: keine neuen Felder (w, width, align, level, tunes) nach dem Speichern', got === want && /Text!/.test(JSON.stringify(bl)), { got, want });

  // --- G5: Rolle Kommentieren - im Block-Menue nur Link und Kommentieren -------------------------------------------
  if (process.env.CK2) {
    const sh = (await api('POST', '/api/notes', { title: 'Geteilt' })).id;
    await api('PATCH', '/api/notes/' + sh, { body: JSON.stringify({ blocks: [P('Lesbar'), P('Zwei')] }), format: 'blocks' });
    await api('PUT', '/api/notes/' + sh + '/shares', { type: 'user', target: 'hk-test-b', role: 'comment' });
    const ctx2 = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
    await ctx2.addCookies([{ name: 'saga', value: process.env.CK2, domain: new URL(BASE).hostname, path: '/' }]);
    const q = await ctx2.newPage(); q.on('pageerror', (e) => errs.push(e.message));
    await q.goto(BASE + '/notes#' + sh); await q.waitForSelector('.ce-paragraph'); await sleep(1000);
    const pr = q.locator('.ce-paragraph', { hasText: 'Lesbar' }).first();
    await pr.click(); await pr.hover(); await sleep(250);
    const btn = q.locator('.ce-toolbar__settings-btn:visible').first();
    let ro = [];
    if (await btn.count()) {
      await btn.click(); await sleep(300);
      ro = await q.evaluate(() => [...document.querySelectorAll('.ce-popover--opened .ce-popover__items > .ce-popover-item:not(.ce-popover-item--hidden) .ce-popover-item__title')].map((x) => x.textContent.trim()));
    }
    await pr.click(); await q.keyboard.press(process.platform === 'darwin' ? 'Meta+/' : 'Control+/'); await sleep(300);
    const am = await q.evaluate(() => [...document.querySelectorAll('.menu:not([hidden]) .menu-item .mi-t')].map((x) => x.textContent.trim()));
    ok(`G5: Kommentieren-Rolle: Cmd+/ ohne Bearbeiten-Aktionen (${am.join(', ') || 'kein Menue'})`,
      am.every((x) => x === 'Link zum Block kopieren' || x === 'Kommentieren'), am);
    await q.keyboard.press('Escape');
    ok(`G5: Kommentieren-Rolle sieht im Block-Menue nur Link/Kommentieren (${ro.join(', ') || 'Griff ausgeblendet'})`,
      ro.indexOf('Farbe') < 0 && ro.indexOf('Duplizieren') < 0 && ro.indexOf('Umwandeln in') < 0 && (ro.length === 0 || (ro.indexOf('Link zum Block kopieren') >= 0 && ro.indexOf('Kommentieren') >= 0)), ro);
    await ctx2.close(); await api('DELETE', '/api/notes/' + sh);
  }

  ok('Keine Seitenfehler / CSP-Verstoesse', !errs.length, errs);
  await api('DELETE', '/api/notes/' + id); await api('DELETE', '/api/notes/' + old);
  await br.close();
  const g = res.filter(Boolean).length;
  console.log('\nGESTALTEN-WEB ' + g + '/' + res.length + (g === res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(g === res.length ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
