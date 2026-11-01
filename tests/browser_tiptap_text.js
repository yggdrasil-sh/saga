// Tiptap-Umbau T4c (static/pm-tools-text.js): Abnahmekarte fuer Tabelle, Code-Block (Hervorhebung, Mermaid) und
// Mathe-Block. Je Typ: Laden ohne Karte, unveraendert byte-gleich (auch Altformate), anlegen ueber "/" bzw. Kuerzel,
// bearbeiten, speichern -> GET -> erwartetes Block-JSON, Undo/Redo, Rolle Lesen, CSP = 0.
// Nur mit SAGA_EDITOR=tiptap (Admin editor.engine = choice; der Test stellt die Konto-Wahl selbst um und zurueck).
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> CK2=<Cookie hk-test-b> HANDLE2=<Handle von hk-test-b>
//        SAGA_EDITOR=tiptap node tests/browser_tiptap_text.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2, HANDLE2 = process.env.HANDLE2;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);
const MOD = process.platform === 'darwin' ? 'Meta' : 'Control';
const J = (x) => JSON.stringify(x);
if (process.env.SAGA_EDITOR !== 'tiptap') { console.log('nur mit SAGA_EDITOR=tiptap'); process.exit(0); }

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function session(ck) {
    const ctx = await br.newContext({ viewport: { width: 1200, height: 900 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: ck, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = [];
    p.on('pageerror', (e) => p.errs.push(e.message));
    await p.addInitScript(() => {
      window.__csp = [];
      document.addEventListener('securitypolicyviolation', (e) => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));
    });
    p.patches = 0;
    p.on('request', (r) => { if (r.method() === 'PATCH' && /\/api\/notes\/\d+$/.test(r.url())) p.patches++; });
    await p.goto(BASE + '/notes'); await sleep(600);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return { ctx, p };
  }
  const A = await session(CK), p = A.p;
  const was = await p.evaluate(() => HK.api('GET', '/api/account/editor'));
  ok('Schalter: Admin erlaubt die Wahl (editor.engine = choice)', was.choice === true, was);
  await D.setEditor(p, 'tiptap');
  const made = [];
  const mk = async (title, blocks) => { const id = (await p.api('POST', '/api/notes', { title })).id; made.push(id); if (blocks) await p.api('PATCH', '/api/notes/' + id, { body: J({ blocks }), format: 'blocks' }); return id; };
  const get = async (id) => JSON.parse((await p.api('GET', '/api/notes/' + id)).body || '{}').blocks || [];
  async function open(id, pg = p) {
    await pg.goto(BASE + '/notes#' + id); await pg.reload();
    for (let i = 0; i < 100; i++) { if (await pg.evaluate((id) => window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector('#pg-editor .pm-doc'), id)) break; await sleep(50); }
    await sleep(700);
    pg.patches = 0;
  }
  async function saved(id, ms = 1500) { await sleep(ms); return get(id); }
  async function until(fn, ms = 8000) { const t = Date.now(); while (Date.now() - t < ms) { if (await fn()) return true; await sleep(150); } return false; }
  const find = (l, id) => l.find((b) => b.id === id);
  const edSave = () => p.evaluate(() => HKPages.k.editor().save().then((o) => JSON.stringify(o.blocks)));
  // Schreibmarke in Zelle (r, c) der Tabelle id (ueber das Dokument, nicht per Klick)
  const cell = (id, r, c, end) => p.evaluate(([id, r, c, end]) => {
    const ed = HKPages.k.editor().editor(); let tp = null;
    ed.state.doc.descendants((n, q) => { if (tp == null && n.type.name === 'table' && n.attrs.id === id) tp = q; });
    const t = ed.state.doc.nodeAt(tp); let q = tp + 1;
    for (let i = 0; i < r; i++) q += t.child(i).nodeSize;
    q += 1; const row = t.child(r); for (let j = 0; j < c; j++) q += row.child(j).nodeSize;
    ed.view.dispatch(ed.state.tr.setSelection(SagaPM.pm.state.TextSelection.create(ed.state.doc, q + 1 + (end ? row.child(c).content.size : 0)))); ed.view.focus();
  }, [id, r, c, !!end]);
  const caretCell = () => p.evaluate(() => {
    const ed = HKPages.k.editor().editor(), $f = ed.state.selection.$from;
    for (let d = $f.depth; d > 2; d--) if ($f.node(d).type.name === 'tcell') return [$f.index(d - 2), $f.index(d - 1)];
    return null;
  });
  async function slash(q, pick) {
    await p.keyboard.type('/' + q, { delay: 20 }); await sleep(300);
    await p.click(`.pm-pop:not([hidden]) .hk-emo-i:has(.hk-at-t:text-is("${pick}"))`); await sleep(300);
  }
  async function blockMenu(id) { await p.evaluate((id) => HKPages.k.doc.menu(id), id); await sleep(250); }
  const menuClick = async (t) => { await p.click(`.menu:not([hidden]) .menu-item:has(.mi-t:text-is("${t}"))`); await sleep(250); };

  // =================================================================================================================
  // 1) Laden: keine Karten, Darstellung, byte-gleich (auch Altformate)
  const L = [
    { id: 't1', type: 'table', data: { withHeadings: true, headCol: true, colWidths: [30, 70], colors: { r: { 1: 'red' }, x: { '0,1': 'blue' } }, stretched: false, content: [['<b>a</b>', 'b'], ['c', 'd']], zz: { fremd: 1 } } },
    { id: 't2', type: 'table', data: { content: [['ohne', 'alles'], ['x', 'y']] } },
    { id: 'c1', type: 'code', data: { code: 'x = "a"  # c\ny = 2', language: 'python' } },
    { id: 'c2', type: 'code', data: { code: 'graph TD; A-->B', language: 'mermaid' } },
    { id: 'c3', type: 'code', data: { code: 'kein view', fremd: [1] } },
    { id: 'm1', type: 'math', data: { tex: '\\frac{a}{b}', fremd: true } },
    { id: 'p9', type: 'paragraph', data: { text: 'Ende' } }
  ];
  const P1 = await mk('TX Laden ' + RUN, L);
  const o1 = await get(P1);
  await open(P1);
  const look = await p.evaluate(() => ({
    cards: [...document.querySelectorAll('#pg-editor .pm-card')].map((c) => c.dataset.blockType),
    tbl: !!document.querySelector('[data-block-id="t1"] table.pm-tbl.pm-tbl-head.pm-tbl-hcol'), red: document.querySelectorAll('[data-block-id="t1"] tr:nth-child(2) td.tbl-bg-red').length,
    blue: !!document.querySelector('[data-block-id="t1"] tr:nth-child(1) td:nth-child(2).tbl-bg-blue'), cols: [...document.querySelectorAll('[data-block-id="t1"] col')].map((c) => c.style.width),
    styleAttr: [...document.querySelectorAll('#pg-editor [style]')].filter((e) => !/^(width|left|--)/.test(e.getAttribute('style'))).map((e) => e.getAttribute('style')).slice(0, 3),
    tk: [...document.querySelectorAll('[data-block-id="c1"] [class^="tk-"]')].map((e) => e.className + ':' + e.textContent),
    lang: document.querySelector('[data-block-id="c1"] select.hk-code-lang').value,
    mmd: document.querySelector('[data-block-id="c2"]').classList.contains('hk-v-preview') }));
  ok('Ohne Bearbeitung: save() byte-gleich mit GET (fremde Felder, Tabelle ohne colWidths, Code ohne view)', (await edSave()) === J(o1), [(await edSave()).slice(0, 300)]);
  await sleep(1200);
  ok('Laden schickt kein PATCH (vor dem Vorschaubild-Nachtrag nach 3 s)', p.patches === 0, p.patches);
  ok('Tabelle, Code, Mathe: keine Karten mehr', !look.cards.length, look.cards);
  ok('Tabelle: Kopfzeile/Kopfspalte, Breiten per CSSOM (30 %/70 %), Farben Zeile/Zelle als Dekoration', look.tbl && look.red === 2 && look.blue && J(look.cols) === J(['30%', '70%']), look);
  ok('Code: Hervorhebung als Dekoration (tk-s/tk-c), Sprache Python', look.tk.includes('tk-s:"a"') && look.tk.includes('tk-c:# c') && look.lang === 'python', look.tk);
  ok('Mermaid ohne view: Vorschau-Ansicht', look.mmd);
  ok('Mathe-Block: KaTeX gerendert', await until(() => p.evaluate(() => !!document.querySelector('[data-block-id="m1"] .katex .mfrac'))));
  ok('Mermaid: Vorschaubild im Rahmen gerendert', await until(() => p.evaluate(() => !!document.querySelector('[data-block-id="c2"] .hk-mmd img.hk-mmd-img')), 15000));
  // Fehlendes Vorschaubild (Mermaid, Formel) holt das Web wie bisher nach: nur render kommt dazu, ohne Undo-Schritt
  ok('Vorschaubild-Nachtrag: c2/m1 bekommen render, sonst alles byte-gleich', await until(async () => {
    const x = await get(P1), strip = (l) => l.map((b) => (b.id === 'c2' || b.id === 'm1') && b.data.render ? Object.assign({}, b, { data: Object.assign({}, b.data, { render: undefined }) }) : b);
    return find(x, 'c2').data.render && find(x, 'm1').data.render && J(strip(x)) === J(o1);
  }, 20000), (await get(P1)).filter((b) => b.id === 'c2' || b.id === 'm1'));
  const o1r = await get(P1);

  // =================================================================================================================
  // 2) Tabelle bearbeiten: Tippen, Tab/Shift-Tab/Pfeile, Enter, Menues, Farben, Breiten, Undo/Redo
  await cell('t2', 0, 0, true); await p.keyboard.type(' eins', { delay: 10 });
  await p.keyboard.press('Tab'); ok('Tab -> naechste Zelle', J(await caretCell()) === J([0, 1]), await caretCell());
  await p.keyboard.press('Tab'); ok('Tab am Zeilenende -> naechste Zeile', J(await caretCell()) === J([1, 0]), await caretCell());
  await p.keyboard.press('Shift+Tab'); ok('Shift+Tab -> vorige Zelle', J(await caretCell()) === J([0, 1]), await caretCell());
  await p.keyboard.press('ArrowDown'); ok('Pfeil runter -> Zelle darunter', J(await caretCell()) === J([1, 1]), await caretCell());
  await p.keyboard.press('ArrowUp'); ok('Pfeil hoch -> Zelle darueber', J(await caretCell()) === J([0, 1]), await caretCell());
  await p.keyboard.press('End'); await p.keyboard.press('Shift+Enter'); await p.keyboard.type('neu', { delay: 10 });
  let b = await saved(P1), t2 = find(b, 't2');
  ok('Tabelle: Tippen + Shift+Enter (Zeilenumbruch in der Zelle) gespeichert, nur content', J(t2.data) === J({ content: [['ohne eins', 'alles<br>neu'], ['x', 'y']] }), t2);
  ok('Andere Bloecke byte-gleich', J(b.filter((x) => x.id !== 't2')) === J(o1r.filter((x) => x.id !== 't2')));
  await cell('t2', 1, 1, true); await p.keyboard.press('Tab'); await p.keyboard.type('z', { delay: 10 });
  b = await saved(P1); t2 = find(b, 't2');
  ok('Tab in der letzten Zelle legt eine Zeile an', t2.data.content.length === 3 && t2.data.content[2][0] === 'z', t2.data.content);
  await p.keyboard.press(MOD + '+z'); await sleep(100); await p.keyboard.press(MOD + '+z');
  b = await saved(P1); t2 = find(b, 't2');
  ok('Undo: Tippen und neue Zeile zurueck', t2.data.content.length === 2, t2.data.content);
  await p.keyboard.press(MOD + '+Shift+z'); await sleep(100); await p.keyboard.press(MOD + '+Shift+z');
  b = await saved(P1);
  ok('Redo: neue Zeile wieder da', find(b, 't2').data.content.length === 3 && find(b, 't2').data.content[2][0] === 'z', find(b, 't2').data.content);
  // Enter wie Editor.js: naechste Zeile, gleiche Spalte
  await cell('t2', 0, 1); await p.keyboard.press('Enter');
  ok('Enter -> Zelle in der naechsten Zeile', J(await caretCell()) === J([1, 1]), await caretCell());
  // Zeilen-/Spaltenmenue an der Tabelle t1 (Farben und Breiten wandern mit)
  await cell('t1', 0, 1);
  ok('Leiste Zeile/Spalte/Zelle erscheint bei Schreibmarke in der Tabelle', await p.evaluate(() => document.querySelector('[data-block-id="t1"]').classList.contains('pm-tbl-on')));
  await p.click('[data-block-id="t1"] .pm-tbl-b[data-a="col"]'); await sleep(200); await menuClick('Spalte links einfügen');
  b = await saved(P1); let t1 = find(b, 't1');
  ok('Spalte links einfuegen: 3 Spalten, Farbe x 0,1 -> 0,2, Breiten neu (Summe 100)', t1.data.content[0].length === 3 && t1.data.colors.x['0,2'] === 'blue' && t1.data.colors.r['1'] === 'red' &&
    t1.data.colWidths.length === 3 && Math.abs(t1.data.colWidths.reduce((a, x) => a + x, 0) - 100) < 1e-6 && t1.data.content[0][0] === '<b>a</b>' && t1.data.content[0][1] === '', t1.data);
  ok('Fremdes Feld der Tabelle bleibt', J(t1.data.zz) === J({ fremd: 1 }));
  await p.keyboard.press(MOD + '+z');
  b = await saved(P1);
  ok('Undo: Spalte einfuegen in einem Schritt zurueck (byte-gleich)', J(find(b, 't1')) === J(find(o1, 't1')), find(b, 't1'));
  await cell('t1', 1, 0); await p.click('[data-block-id="t1"] .pm-tbl-b[data-a="row"]'); await sleep(200); await menuClick('Nach oben');
  b = await saved(P1); t1 = find(b, 't1');
  ok('Zeile nach oben: Inhalt und Zeilenfarbe wandern mit', J(t1.data.content) === J([['c', 'd'], ['<b>a</b>', 'b']]) && t1.data.colors.r['0'] === 'red' && t1.data.colors.x['1,1'] === 'blue', t1.data);
  await cell('t1', 0, 0); await p.click('[data-block-id="t1"] .pm-tbl-b[data-a="cell"]'); await sleep(200); await menuClick(await p.evaluate(() => HKPages.k.colorNames.green));
  b = await saved(P1); t1 = find(b, 't1');
  ok('Zelle einfaerben (grün) -> colors.x["0,0"]', t1.data.colors.x['0,0'] === 'green', t1.data.colors);
  await cell('t1', 0, 1); await p.click('[data-block-id="t1"] .pm-tbl-b[data-a="col"]'); await sleep(200); await menuClick('Spalte löschen');
  b = await saved(P1); t1 = find(b, 't1');
  ok('Spalte loeschen: Breiten fallen mit (1 Spalte = 100), Zellfarbe in Spalte 1 weg', t1.data.content[0].length === 1 && J(t1.data.colWidths) === J([100]) && !(t1.data.colors.x || {})['1,1'], t1.data);
  await p.keyboard.press(MOD + '+z'); await sleep(100); await p.keyboard.press(MOD + '+z'); await sleep(100); await p.keyboard.press(MOD + '+z');
  b = await saved(P1);
  ok('Undo dreimal: Tabelle wie geladen', J(find(b, 't1')) === J(find(o1, 't1')), find(b, 't1'));
  // Block-Menue: Kopfzeile/Kopfspalte/volle Breite
  await blockMenu('t2');
  ok('Block-Menue: Titel "Tabelle"', /Tabelle/.test(await p.textContent('.menu:not([hidden]) .pm-blockmenu')));
  await menuClick('Mit Kopfzeile'); await blockMenu('t2'); await menuClick('Ohne Kopfzeile'); await blockMenu('t2'); await menuClick('Erste Spalte als Kopf'); await blockMenu('t2'); await menuClick('Volle Breite ein');
  b = await saved(P1); t2 = find(b, 't2');
  ok('Block-Menue: withHeadings false, headCol true, stretched true', t2.data.withHeadings === false && t2.data.headCol === true && t2.data.stretched === true, t2.data);
  // Spaltenbreite ziehen: ein Zug = eine Transaktion, Doppelklick = gleich
  await cell('t2', 0, 0);
  const rh = await p.evaluate(() => { const r = document.querySelector('[data-block-id="t2"] .pm-tbl-rh').getBoundingClientRect(); return [r.left + r.width / 2, r.top + 10]; });
  p.patches = 0;
  await p.mouse.move(rh[0], rh[1]); await p.mouse.down(); await p.mouse.move(rh[0] - 150, rh[1], { steps: 8 }); await p.mouse.up();
  b = await saved(P1); t2 = find(b, 't2');
  ok('Spaltenbreite ziehen: colWidths gesetzt (5-90 %, Summe 100), ein PATCH', Array.isArray(t2.data.colWidths) && t2.data.colWidths.length === 2 && t2.data.colWidths[0] < 50 &&
    Math.abs(t2.data.colWidths[0] + t2.data.colWidths[1] - 100) < 1e-6 && p.patches === 1, [t2.data.colWidths, p.patches]);
  await p.dblclick('[data-block-id="t2"] .pm-tbl-rh');
  b = await saved(P1);
  ok('Doppelklick auf den Griff: gleich breit (kein colWidths)', !('colWidths' in find(b, 't2').data), find(b, 't2').data);
  ok('Speichern -> GET identisch mit Editorstand', (await edSave()) === J(b));

  // =================================================================================================================
  // 3) Code bearbeiten: Tippen, Tab, Enter, dreimal Enter, Sprache, Mermaid-Ansicht
  const P2 = await mk('TX Code ' + RUN, [{ id: 's0', type: 'paragraph', data: { text: 'Start' } }]);
  await open(P2);
  await D.focus(p, 's0', 'end'); await p.keyboard.press('Enter');
  await p.keyboard.type('```python ', { delay: 15 });
  let cur = await p.evaluate(() => { const c = HKPages.k.doc.current(); return c && c.type; });
  ok('Kuerzel ```python + Leerzeichen -> Code-Block (Python)', cur === 'code', cur);
  await p.keyboard.type('def f():', { delay: 10 }); await p.keyboard.press('Enter'); await p.keyboard.press('Tab'); await p.keyboard.type('return "x"', { delay: 10 });
  b = await saved(P2);
  let c = b[1];
  ok('Code: Enter = Zeile, Tab = 2 Leerzeichen, language python', c && c.type === 'code' && c.data.code === 'def f():\n  return "x"' && c.data.language === 'python' && !('view' in c.data), c);
  ok('Code: Hervorhebung zieht beim Tippen nach', await p.evaluate((id) => !!document.querySelector('[data-block-id="' + id + '"] .tk-k') && !!document.querySelector('[data-block-id="' + id + '"] .tk-s'), c.id));
  ok('Code: keine Marken (Strg+B wirkt nicht)', await (async () => { await p.keyboard.press('Shift+Home'); await p.keyboard.press(MOD + '+b'); const x = (await saved(P2, 900))[1]; return x.data.code === 'def f():\n  return "x"'; })());
  await p.keyboard.press('End');
  await p.keyboard.press('Shift+Tab');
  b = await saved(P2); ok('Shift+Tab rueckt aus', b[1].data.code === 'def f():\nreturn "x"', b[1].data.code);
  await p.keyboard.press(MOD + '+z'); await sleep(200);
  b = await saved(P2); ok('Undo: Ausruecken zurueck', b[1].data.code === 'def f():\n  return "x"', b[1].data.code);
  await p.keyboard.press('End'); await p.keyboard.press('Enter'); await p.keyboard.press('Enter'); await p.keyboard.press('Enter');
  await p.keyboard.type('danach', { delay: 10 });
  b = await saved(P2);
  ok('Dreimal Enter verlaesst den Code-Block (Leerzeilen fallen weg), Absatz dahinter', b.length === 3 && b[1].data.code === 'def f():\n  return "x"' && b[2].type === 'paragraph' && b[2].data.text === 'danach', b);
  // Slash: Code, Mod+Enter verlaesst
  await p.keyboard.press('Enter'); await slash('code', 'Code');
  await p.keyboard.type('a', { delay: 10 }); await p.keyboard.press(MOD + '+Enter'); await p.keyboard.type('b', { delay: 10 });
  b = await saved(P2);
  ok('Slash "Code" legt einen Code-Block an (leerer Absatz ersetzt), Strg+Enter verlaesst', b[3] && b[3].type === 'code' && b[3].data.code === 'a' && b[4] && b[4].data.text === 'b', b.slice(3));
  // Sprache waehlen + Mermaid
  const cid = b[3].id;
  await p.selectOption('[data-block-id="' + cid + '"] select.hk-code-lang', 'mermaid');
  await D.focus(p, cid, 'end');
  await p.evaluate((id) => { const ed = HKPages.k.editor().editor(); const x = HKPages.k.doc.byId(id); let pos; ed.state.doc.descendants((n, q) => { if (n.attrs && n.attrs.id === id) pos = q; });
    const n = ed.state.doc.nodeAt(pos); ed.view.dispatch(ed.state.tr.insertText('graph LR; X-->Y', pos + 1, pos + 1 + n.content.size)); }, cid);
  b = await saved(P2); c = find(b, cid);
  ok('Sprache Mermaid: language mermaid, view split', c.data.language === 'mermaid' && c.data.view === 'split' && c.data.code === 'graph LR; X-->Y', c.data);
  ok('Mermaid-Vorschau neben dem Text (Ansicht geteilt)', await until(() => p.evaluate((id) => { const d = document.querySelector('[data-block-id="' + id + '"]'); return d.classList.contains('hk-v-split') && !!d.querySelector('.hk-mmd img'); }, cid), 15000));
  await p.click('[data-block-id="' + cid + '"] .hk-code-vb[data-v="preview"]');
  b = await saved(P2); ok('Ansicht Vorschau gespeichert', find(b, cid).data.view === 'preview', find(b, cid).data);
  ok('Vorschaubild fuer die Apps (render.png, h = SHA-256 des Quelltexts)', await until(async () => { const x = find(await get(P2), cid).data.render; return !!(x && /^\/files\//.test(x.png) && !x.stale && /^[0-9a-f]{16}$/.test(x.h)); }, 20000), find(await get(P2), cid).data.render);
  await blockMenu(cid); await p.hover('.menu:not([hidden]) .menu-item:has(.mi-t:text-is("Programmiersprache"))'); await sleep(300);
  await p.click('.menu-sub:not([hidden]) .menu-item:has(.mi-t:text-is("JSON")), .menu:not([hidden]) .menu-item:has(.mi-t:text-is("JSON"))'); await sleep(300);
  b = await saved(P2); c = find(b, cid);
  ok('Block-Menue Programmiersprache JSON: view/render entfallen', c.data.language === 'json' && !('view' in c.data) && !('render' in c.data), c.data);
  ok('Speichern -> GET identisch mit Editorstand (Code)', (await edSave()) === J(b));

  // =================================================================================================================
  // 4) Mathe-Block: Slash, tippen, Undo
  await D.focus(p, b[b.length - 1].id, 'end'); await p.keyboard.press('Enter');
  await slash('formel', 'Formel (Mathe)');
  ok('Slash "Formel (Mathe)": Eingabefeld offen', await p.evaluate(() => document.activeElement && document.activeElement.classList.contains('hk-math-ta')));
  await p.keyboard.type('x^2', { delay: 20 }); await p.keyboard.press('Escape');
  b = await saved(P2); let m = b.find((x) => x.type === 'math');
  ok('Mathe-Block gespeichert {tex: "x^2"}', m && m.data.tex === 'x^2', b.slice(-2));
  ok('Esc verlaesst das Feld, Formel gerendert', await until(() => p.evaluate((id) => { const d = document.querySelector('[data-block-id="' + id + '"]'); return !d.classList.contains('editing') && !!d.querySelector('.katex'); }, m.id)));
  await p.keyboard.type('weiter', { delay: 10 });
  b = await saved(P2);
  ok('Nach Esc: Schreibmarke hinter der Formel (neuer Absatz)', b[b.length - 1].type === 'paragraph' && b[b.length - 1].data.text === 'weiter', b.slice(-2));
  await p.click('[data-block-id="' + m.id + '"] .hk-math-view'); await p.keyboard.press('End'); await p.keyboard.type('+1', { delay: 20 }); await p.keyboard.press('Escape');
  b = await saved(P2); ok('Formel bearbeitet (Klick auf die Darstellung)', find(b, m.id).data.tex === 'x^2+1', find(b, m.id).data);
  await p.evaluate(() => document.activeElement && document.activeElement.blur());
  await D.focus(p, b[b.length - 1].id, 'end'); for (let i = 0; i < 4; i++) { await p.keyboard.press(MOD + '+z'); await sleep(120); }
  b = await saved(P2); ok('Undo: Formel-Text zurueck (tex atomar als Attribut)', find(b, m.id) ? find(b, m.id).data.tex !== 'x^2+1' : true, find(b, m.id));
  ok('Mathe/Code: keine Seitenfehler', !p.errs.length, p.errs);
  // Knotenauswahl auf Tabelle/Code (Klick auf den Rand) + Tippen: Block bleibt (Pruefer T4c, wie das Bild in 8dd6a80)
  {
    const PS = await mk('TX Auswahl ' + RUN, [{ id: 'st', type: 'table', data: { content: [['a', 'b']] } },
      { id: 'sc', type: 'code', data: { code: 'x = 1', language: 'python' } }, { id: 'sp', type: 'paragraph', data: { text: 'Ende' } }]);
    const o = await get(PS);
    await open(PS);
    for (const id of ['st', 'sc']) {
      await p.evaluate((id) => { const ed = HKPages.k.editor().editor(); let q = null; ed.state.doc.descendants((n, x) => { if (q == null && n.attrs && n.attrs.id === id) q = x; });
        ed.view.dispatch(ed.state.tr.setSelection(SagaPM.pm.state.NodeSelection.create(ed.state.doc, q))); ed.view.focus(); }, id);
      await p.keyboard.type('q');
    }
    await sleep(1300);
    ok('Ausgewaehlte Tabelle/Code: Tippen ersetzt sie nicht', J(await get(PS)) === J(o), (await get(PS)).map((x) => x.type));
  }

  // =================================================================================================================
  // 5) Rolle Lesen: anzeigen ohne Bearbeiten
  if (CK2 && HANDLE2) {
    await p.api('PUT', '/api/notes/' + P1 + '/shares', { type: 'user', target: HANDLE2, role: 'view' });
    const B = await session(CK2), q = B.p;
    await D.setEditor(q, 'tiptap');
    await open(P1, q);
    await until(() => q.evaluate(() => !!document.querySelector('[data-block-id="m1"] textarea')));
    const r0 = await q.evaluate(() => ({ ce: document.querySelector('#pg-editor .pm-doc').getAttribute('contenteditable'), cards: document.querySelectorAll('#pg-editor .pm-card').length,
      sel: document.querySelector('[data-block-id="c1"] select.hk-code-lang').disabled, ta: !!(document.querySelector('[data-block-id="m1"] textarea') || {}).readOnly, ids: HKPages.k.doc.blocks().map((x) => x.id).join(), kind: HKPages.k.doc.kind,
      bar: getComputedStyle(document.querySelector('[data-block-id="t1"] .pm-tbl-bar')).display, katex: !!document.querySelector('[data-block-id="m1"] .katex') }));
    ok('Lesen: nicht bearbeitbar, keine Karten, Sprache gesperrt, Formel nur lesend, keine Tabellen-Leiste', r0.ce === 'false' && !r0.cards && r0.sel && r0.ta && r0.bar === 'none', r0);
    await q.click('[data-block-id="t2"] td'); await q.keyboard.type('xx');
    await q.click('[data-block-id="m1"] .hk-math-view'); await q.keyboard.type('yy');
    ok('Lesen: Code-Kopf (Sprache/Ansicht) ausgeblendet wie im klassischen Editor', await q.evaluate(() => !document.querySelector('[data-block-id="c2"] .hk-code-head').offsetParent));
    await q.click('[data-block-id="c1"] code'); await q.keyboard.type('zz'); await sleep(1300);
    const m1b = find(await get(P1), 'm1');
    await q.click('[data-block-id="t2"] td'); await q.keyboard.type('xx'); await sleep(1300);
    ok('Lesen: Tippen/Klicks aendern nichts, kein PATCH', q.patches === 0 && J(find(await get(P1), 'm1')) === J(m1b), q.patches);
    ok('Lesen: keine Seitenfehler, CSP = 0', !q.errs.length && !(await q.evaluate(() => window.__csp.length)), [q.errs, await q.evaluate(() => window.__csp)]);
    await B.ctx.close();
  }

  // =================================================================================================================
  // 6) Kopieren/Einfuegen einer Tabelle (Block-JSON) und Grenze 20 Spalten
  // Tabelle ueber "/": 2 x 3, Kopfzeile, Schreibmarke in der ersten Zelle
  const P4 = await mk('TX Slash ' + RUN, [{ id: 'q0', type: 'paragraph', data: { text: '' } }]);
  await open(P4);
  await D.focus(p, 'q0', 'end'); await slash('tabelle', 'Tabelle');
  ok('Slash "Tabelle": Schreibmarke in Zelle 1/1', J(await caretCell()) === J([0, 0]), await caretCell());
  await p.keyboard.type('k1', { delay: 10 }); await p.keyboard.press('Tab'); await p.keyboard.type('k2', { delay: 10 });
  b = await saved(P4);
  ok('Slash "Tabelle": leerer Absatz ersetzt (ID bleibt), {withHeadings, stretched, content 2x3}', b.length === 1 && b[0].id === 'q0' && b[0].type === 'table' &&
    J(b[0].data) === J({ withHeadings: true, stretched: false, content: [['k1', 'k2', ''], ['', '', '']] }), b);
  for (let i = 0; i < 6; i++) { await p.keyboard.press(MOD + '+z'); await sleep(80); }
  b = await saved(P4);
  ok('Undo bis vor die Tabelle: wieder ein leerer Absatz', b.length === 1 && b[0].type === 'paragraph', b);

  const P3 = await mk('TX Grenze ' + RUN, [{ id: 'g1', type: 'table', data: { withHeadings: false, content: [Array.from({ length: 20 }, (_, i) => String(i))] } }]);
  await open(P3);
  await cell('g1', 0, 19);
  ok('20 Spalten: Knopf "+ Spalte" ausgeblendet', await p.evaluate(() => document.querySelector('[data-block-id="g1"] .pm-tbl-addc').disabled));
  await p.click('[data-block-id="g1"] .pm-tbl-b[data-a="col"]'); await sleep(200);
  ok('20 Spalten: Menue ohne "Spalte rechts einfügen"', !(await p.$('.menu:not([hidden]) .menu-item:has(.mi-t:text-is("Spalte rechts einfügen"))')));
  await p.keyboard.press('Escape');

  const csp = await p.evaluate(() => window.__csp);
  ok('CSP-Verletzungen = 0 (securitypolicyviolation)', !csp.length, csp);
  ok('Keine Seitenfehler (pageerror)', !p.errs.length, p.errs);
  await D.setEditor(p, was.tiptap ? 'tiptap' : 'editorjs');
  for (const id of made) await p.api('DELETE', '/api/notes/' + id).catch(() => {});
  await br.close();
  const n = res.filter(Boolean).length;
  console.log(`\n${n}/${res.length} ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
  process.exit(n === res.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
