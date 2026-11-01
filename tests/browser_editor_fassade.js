// Saga 2.5 (Tiptap-Umbau T1): Editor-Fassade HKPages.k.doc und data-block-* auf einer Seite mit Absatz, Liste, Spalten
// (mit Bloecken), Toggle (aufgeklappt) und Callout - blocks() Reihenfolge/Eltern, byId, insertAfter/replace/remove (auch
// in einer Spalte), select/selected, focus/current, onChange; danach Speichern -> GET -> Inhalt stimmt.
// Laeuft in T2 unveraendert gegen Tiptap (nur Fassade + data-block-*).
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> node tests/browser_editor_fassade.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
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
  await p.goto(BASE + '/notes');
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const P = (id, t) => ({ id, type: 'paragraph', data: { text: t } });
  const blocksOf = async (id) => JSON.parse((await api('GET', '/api/notes/' + id)).body).blocks;
  const doc = [
    P('p1', 'Erster Absatz'),
    { id: 'l1', type: 'list', data: { style: 'unordered', meta: {}, items: [{ content: 'eins', meta: {}, items: [] }, { content: 'zwei', meta: {}, items: [] }] } },
    { id: 'c1', type: 'columns', data: { cols: [{ blocks: [P('x1', 'links oben'), P('x2', 'links unten')] }, { blocks: [P('y1', 'rechts')] }] } },
    { id: 't1', type: 'toggle', data: { title: 'Aufklapper', open: true, blocks: [P('z1', 'im Toggle')] } },
    { id: 'k1', type: 'callout', data: { emoji: '💡', text: 'Hinweis hier' } },
    P('p2', 'Letzter Absatz'),
  ];
  const id = (await api('POST', '/api/notes', { title: 'Fassade' })).id;
  await api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks: doc }), format: 'blocks' });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
  await p.waitForSelector(D.block('z1')); await sleep(1200);

  // --- data-block-* an allen Bloecken, auch verschachtelt ----------------------------------------------------------
  const want = ['p1', 'l1', 'c1', 'x1', 'x2', 'y1', 't1', 'z1', 'k1', 'p2'];
  const tagged = await p.evaluate(() => [...document.querySelectorAll('#pg-editor [data-block-id]')].map((b) => b.getAttribute('data-block-id') + ':' + b.getAttribute('data-block-type')));
  ok('data-block-id/-type an allen Bloecken (auch in Spalten/Toggle)', JSON.stringify(tagged) === JSON.stringify(
    ['p1:paragraph', 'l1:list', 'c1:columns', 'x1:paragraph', 'x2:paragraph', 'y1:paragraph', 't1:toggle', 'z1:paragraph', 'k1:callout', 'p2:paragraph']), tagged);

  // --- blocks(): Reihenfolge, Eltern, Slot -------------------------------------------------------------------------
  let bl = await D.blocks(p);
  ok('blocks(): Dokumentreihenfolge', JSON.stringify(bl.map((b) => b.id)) === JSON.stringify(want), bl.map((b) => b.id));
  const par = Object.fromEntries(bl.map((b) => [b.id, [b.parent, b.slot]]));
  ok('blocks(): Elternblock + Slot', par.p1[0] === null && par.x1[0] === 'c1' && par.x1[1] === 0 && par.x2[1] === 0 && par.y1[0] === 'c1' && par.y1[1] === 1
    && par.z1[0] === 't1' && par.k1[0] === null, par);
  ok('blocks(): Typen', bl.find((b) => b.id === 'l1').type === 'list' && bl.find((b) => b.id === 'k1').type === 'callout', bl.map((b) => b.type));

  // --- byId ----------------------------------------------------------------------------------------------------
  const by = await p.evaluate(async () => { const d = HKPages.k.doc, b = d.byId('x2'), j = await b.json();
    return { id: b.id, type: b.type, parent: b.parent, el: b.el.getAttribute('data-block-id'), data: await b.data, j, none: d.byId('gibtsnicht') }; });
  ok('byId: Element, Typ, Eltern, Daten', by.id === 'x2' && by.type === 'paragraph' && by.parent === 'c1' && by.el === 'x2' && by.data.text === 'links unten'
    && by.j.type === 'paragraph' && by.j.id === 'x2' && by.none === null, by);
  ok('byId: Liste liefert Block-JSON', await p.evaluate(async () => { const j = await HKPages.k.doc.byId('l1').json(); return j.data.items.length === 2 && j.data.style === 'unordered'; }));

  // --- onChange ----------------------------------------------------------------------------------------------
  await p.evaluate(() => { window.__chg = []; window.__off = HKPages.k.doc.onChange((e) => window.__chg.push(e.ids)); });

  // --- insertAfter: oberste Ebene, in Spalte, Ende -----------------------------------------------------------------
  const ins = await p.evaluate(async () => {
    const d = HKPages.k.doc;
    const a = await d.insertAfter('p1', [{ type: 'paragraph', data: { text: 'nach p1' }, id: 'n1' }]);
    const b = await d.insertAfter('x1', [{ type: 'header', data: { text: 'Spalte Titel', level: 3 }, id: 'n2' }]);
    const c = await d.insertAfter(null, [{ type: 'paragraph', data: { text: 'ganz unten' } }]);
    const e = await d.insertAfter({ parent: 'c1', slot: 1, at: 'start' }, [{ type: 'paragraph', data: { text: 'rechts oben' }, id: 'n4' }]);
    return [a, b, c, e];
  });
  bl = await D.blocks(p);
  const ids = bl.map((b) => b.id);
  ok('insertAfter: Rueckgabe neue IDs (vorgegebene bleiben)', ins[0][0] === 'n1' && ins[1][0] === 'n2' && /^[A-Za-z0-9_-]{1,40}$/.test(ins[2][0]) && ins[3][0] === 'n4', ins);
  ok('insertAfter: Stellen (oben, in Spalte, Ende, Spaltenanfang)', ids.indexOf('n1') === ids.indexOf('p1') + 1 && ids.indexOf('n2') === ids.indexOf('x1') + 1
    && ids[ids.length - 1] === ins[2][0] && ids.indexOf('n4') === ids.indexOf('y1') - 1, ids);
  const n2 = bl.find((b) => b.id === 'n2'), n4 = bl.find((b) => b.id === 'n4');
  ok('insertAfter: Eltern in der Spalte', n2.parent === 'c1' && n2.slot === 0 && n2.type === 'header' && n4.parent === 'c1' && n4.slot === 1, [n2, n4]);
  ok('insertAfter: neue Bloecke tragen data-block-*', await p.evaluate(() => ['n1', 'n2', 'n4'].every((i) => { const e = document.querySelector('#pg-editor [data-block-id="' + i + '"]'); return e && e.getAttribute('data-block-type'); })));
  ok('onChange feuert nach Aenderungen', await until(() => p.evaluate(() => window.__chg.length > 0)), await p.evaluate(() => window.__chg));

  // --- replace: in place (gleicher Typ, gleiche ID) und Typwechsel in der Spalte ------------------------------------
  const rep = await p.evaluate(async () => {
    const d = HKPages.k.doc;
    const a = await d.replace(['p2'], [{ id: 'p2', type: 'paragraph', data: { text: 'Letzter geaendert' } }]);
    const b = await d.replace(['x2'], [{ type: 'quote', data: { text: 'Zitat in Spalte', caption: '', alignment: 'left' }, id: 'x2' }]);
    const c = await d.replace(['n1'], [{ type: 'paragraph', data: { text: 'r1' }, id: 'r1' }, { type: 'paragraph', data: { text: 'r2' }, id: 'r2' }]);
    return [a, b, c];
  });
  bl = await D.blocks(p);
  const x2 = bl.find((b) => b.id === 'x2');
  ok('replace: Rueckgabe', JSON.stringify(rep) === JSON.stringify([['p2'], ['x2'], ['r1', 'r2']]), rep);
  ok('replace: in place, Typwechsel in Spalte, 1 -> 2', x2 && x2.type === 'quote' && x2.parent === 'c1' && /Zitat in Spalte/.test(x2.text)
    && bl.find((b) => b.id === 'p2').text === 'Letzter geaendert' && !bl.some((b) => b.id === 'n1')
    && bl.map((b) => b.id).join(',').includes('p1,r1,r2,l1'), bl.map((b) => b.id + ':' + b.type));

  // --- remove: oben und in der Spalte ------------------------------------------------------------------------------
  const rm = await p.evaluate(() => HKPages.k.doc.remove(['r2', 'n2', 'gibtsnicht']));
  bl = await D.blocks(p);
  ok('remove: entfernt, unbekannte IDs ignoriert', JSON.stringify(rm) === JSON.stringify(['r2', 'n2']) && !bl.some((b) => b.id === 'r2' || b.id === 'n2'), rm);

  // --- select / selected (auch ueber Ebenen) -----------------------------------------------------------------------
  await D.select(p, ['p1', 'l1']); await sleep(200);
  ok('select/selected: zwei Bloecke oben', JSON.stringify(await D.selected(p)) === JSON.stringify(['p1', 'l1']));
  ok('select: Auswahl-Leiste sichtbar', await until(() => p.evaluate(() => { const b = document.querySelector('.hk-sel-bar'); return b && !b.hidden && /2 Blöcke/.test(b.textContent); }), 3000));
  await D.select(p, ['x1', 'p2', 'z1']);
  ok('selected: ueber Ebenen in Dokumentreihenfolge', JSON.stringify(await D.selected(p)) === JSON.stringify(['x1', 'z1', 'p2']), await D.selected(p));
  await D.select(p, []);
  ok('select([]) hebt die Auswahl auf', (await D.selected(p)).length === 0);

  // --- focus / current -------------------------------------------------------------------------------------------
  ok('focus(y1, end) -> current() = y1 (in Spalte)', await p.evaluate(() => HKPages.k.doc.focus('y1', 'end')) && (await D.current(p)) === 'y1', await D.current(p));
  await p.keyboard.type(' +getippt', { delay: 15 });
  ok('Tippen nach focus(end) landet am Blockende', /rechts \+getippt$/.test(await D.textOfBlock(p, 'y1')), await D.textOfBlock(p, 'y1'));
  ok('focus(z1, start) im Toggle', await p.evaluate(() => HKPages.k.doc.focus('z1', 'start')) && (await D.current(p)) === 'z1');
  await D.typeEnd(p, 'k1', ' !', 15);
  ok('Helfer typeEnd im Callout -> current() = k1', (await D.current(p)) === 'k1' && /Hinweis hier !$/.test(await D.textOfBlock(p, 'k1')), await D.textOfBlock(p, 'k1'));
  ok('Helfer count/find', (await D.count(p, { parent: 'c1' })) === 4 && (await D.find(p, { text: 'Zitat in Spalte' })) === 'x2' && (await D.count(p, { top: true, type: 'paragraph' })) >= 3);

  // --- onChange abmelden -----------------------------------------------------------------------------------------
  const before = await p.evaluate(() => { window.__off(); return window.__chg.length; });
  await p.evaluate(() => HKPages.k.doc.insertAfter('p1', [{ type: 'paragraph', data: { text: 'nach Abmelden' }, id: 'q9' }]));
  await sleep(900);
  ok('onChange: Abmeldefunktion wirkt', (await p.evaluate(() => window.__chg.length)) === before);

  // --- Speichern -> GET ----------------------------------------------------------------------------------------
  const saved = await until(async () => {
    const b = await blocksOf(id);
    const c = b.find((x) => x.type === 'columns');
    return b.some((x) => x.id === 'q9') && c && JSON.stringify(c).includes('getippt') && JSON.stringify(b).includes('Hinweis hier !') && b;
  }, 12000);
  ok('Speichern: Seite gespeichert', !!saved);
  if (saved) {
    const top = saved.map((b) => b.id);
    const col = saved.find((b) => b.type === 'columns'), tg = saved.find((b) => b.type === 'toggle');
    ok('GET: oberste Ebene', top.slice(0, 4).join(',') === 'p1,q9,r1,l1' && top.includes('c1') && top.includes('t1') && top.includes('k1'), top);
    ok('GET: Spalten (Zitat, eingefuegt, getippt)', col.data.cols[0].blocks.map((b) => b.id + ':' + b.type).join(',') === 'x1:paragraph,x2:quote'
      && col.data.cols[1].blocks.map((b) => b.id).join(',') === 'n4,y1' && col.data.cols[1].blocks[1].data.text === 'rechts +getippt', col.data.cols);
    ok('GET: Toggle-Inhalt unveraendert, Callout getippt', tg.data.blocks[0].data.text === 'im Toggle' && saved.find((b) => b.id === 'k1').data.text === 'Hinweis hier !', [tg, saved.find((b) => b.id === 'k1')]);
    ok('GET: replace in place', saved.find((b) => b.id === 'p2').data.text === 'Letzter geaendert');
  }
  // nach Neuladen wieder alle Bloecke markiert
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector(D.block('z1')); await sleep(1000);
  const untagged = await p.evaluate((s) => [...document.querySelectorAll(s)].filter((b) => !b.hasAttribute('data-block-id')).length, D.UI.nativeBlock);   // Kontrolle
  ok('Neu geladen: kein Block ohne data-block-id', untagged === 0 && (await D.count(p)) >= 12, untagged);
  ok('keine Seitenfehler', errs.length === 0, errs);
  await api('DELETE', '/api/notes/' + id);
  await br.close();
  const n = res.filter(Boolean).length;
  console.log(`\nEDITOR-FASSADE ${n}/${res.length} OK${n === res.length ? ' – ALLES GRUEN' : ''}`);
  process.exit(n === res.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
