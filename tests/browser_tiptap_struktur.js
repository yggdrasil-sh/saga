// Tiptap-Umbau T3 (Struktur, static/pm-struktur.js): Abnahmekarten fuer Spalten, Toggle, Block-Griffe/Ziehen/Rand-Drop,
// Block-Menue, Mehrfachauswahl (ueber Ebenen und Spalten, T-E5 A), synchronisierte Bloecke, Konfliktblock. Je Teil: anlegen
// ueber "/", bearbeiten, Rollen (Lesen/gesperrt/fremd), kopieren/einfuegen, Undo/Redo als EIN Schritt (auch ueber
// Spaltengrenzen), speichern -> GET identisch, unveraenderte Strukturen byte-gleich, beim Laden kein PATCH; CSP = 0,
// Speicher nach Seitenwechseln.
// Braucht den Server mit Admin editor.engine = choice (der Test stellt die Konto-Wahl selbst auf Tiptap und am Ende zurueck).
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> CK2=<Cookie hk-test-b> HANDLE2=<Handle von hk-test-b>
//        node tests/browser_tiptap_struktur.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2, HANDLE2 = process.env.HANDLE2;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);
const MOD = process.platform === 'darwin' ? 'Meta' : 'Control';
const J = (x) => JSON.stringify(x);

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function session(ck, vp) {
    const ctx = await br.newContext({ viewport: vp || { width: 1200, height: 900 }, locale: 'de-DE' });
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
  const mk = async (title, blocks, pg = p) => { const id = (await pg.api('POST', '/api/notes', { title })).id; made.push(id); if (blocks) await pg.api('PATCH', '/api/notes/' + id, { body: J({ blocks }), format: 'blocks' }); return id; };
  const get = async (id) => JSON.parse((await p.api('GET', '/api/notes/' + id)).body || '{}').blocks || [];
  async function open(id, pg = p) {
    await pg.goto(BASE + '/notes#' + id); await pg.reload();
    for (let i = 0; i < 100; i++) { if (await pg.evaluate((id) => window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector('#pg-editor .pm-doc'), id)) break; await sleep(50); }
    await sleep(700);
    pg.patches = 0;
  }
  async function saved(id, ms = 1500) { await sleep(ms); return get(id); }
  const ids = (l) => l.map((b) => b.id);
  const colIds = (b) => b && b.data.cols.map((c) => c.blocks.map((x) => x.id));
  const find = (l, id) => l.find((b) => b.id === id);
  const sel = () => p.evaluate(() => HKPages.k.doc.selected().map((b) => b.id));
  const facade = () => p.evaluate(() => HKPages.k.doc.blocks().map((b) => b.id + ':' + b.type + ':' + (b.parent == null ? '-' : b.parent + '/' + b.slot)));
  async function hoverBlock(id, part) {
    const r = await p.evaluate(([id, part]) => {
      const b = document.querySelector('[data-block-id="' + id + '"]'), e = (part && b.querySelector(part)) || b.querySelector('.pm-in') || b, x = e.getBoundingClientRect();
      return [x.left + Math.min(30, x.width / 2), x.top + Math.min(10, x.height / 2)];
    }, [id, part || null]);
    await p.mouse.move(r[0], r[1]); await sleep(250);
  }
  // Block am Griff ⋮⋮ ziehen und bei (x, y) loslassen; -> {ghost, line, v} waehrend des Ziehens
  async function dragTo(id, tx, ty, part) {
    await hoverBlock(id, part);
    const h = await p.evaluate(() => { const r = document.querySelector('.pm-handle-menu').getBoundingClientRect(); return [r.left + 12, r.top + 12]; });
    await p.mouse.move(h[0], h[1]); await p.mouse.down(); await p.mouse.move(h[0] + 8, h[1] + 8, { steps: 2 }); await p.mouse.move(tx, ty, { steps: 8 });
    const st = await p.evaluate(() => { const l = document.querySelector('.pm-dropline'); return { ghost: !!document.querySelector('.pm-ghost'), line: !!l && !l.hidden, v: !!l && l.classList.contains('pm-dropline-v'),
      w: l && l.getBoundingClientRect().width, h: l && l.getBoundingClientRect().height }; });
    await p.mouse.up(); await sleep(200); return st;
  }
  const rect = (id, part) => p.evaluate(([id, part]) => { const b = document.querySelector('[data-block-id="' + id + '"]'); const e = part ? b.querySelector(part) : b; const r = e.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height }; }, [id, part || null]);

  // =================================================================================================================
  // 1) Spalten: Laden, Darstellung, Durchreichung
  const COLS = [
    { id: 'p1', type: 'paragraph', data: { text: 'Oben' } },
    { id: 'cl', type: 'columns', data: { cols: [
      { w: 0.6, blocks: [{ id: 'a1', type: 'paragraph', data: { text: 'Links <b>eins</b>' } }, { id: 'a2', type: 'header', data: { text: 'Links Titel', level: 3 } },
        { id: 'al', type: 'list', data: { style: 'unordered', meta: {}, items: [{ content: 'punkt', meta: {}, items: [] }] } }] },
      { w: 0.4, blocks: [{ id: 'b1', type: 'paragraph', data: { text: 'Rechts' } }, { id: 'w1', type: 'widget', data: { kind: 'clock', zz: 1 } },
        { id: 'c1', type: 'code', data: { code: 'x = 1', language: 'python' } }, { id: 'bt', type: 'toggle', data: { title: 'In Spalte', open: true, blocks: [{ id: 'bt1', type: 'paragraph', data: { text: 'tief' } }] } }] }] } },
    { id: 'p9', type: 'paragraph', data: { text: 'Unten' } }
  ];
  const P1 = await mk('TS Spalten ' + RUN, COLS);
  const o1 = await get(P1);
  await open(P1);
  const f1 = await facade();
  ok('Spalten: echter Knoten (keine Karte), Kinder in der Fassade mit parent/slot', !(await p.$('[data-block-id="cl"].pm-card')) &&
    ['a1:paragraph:cl/0', 'a2:header:cl/0', 'b1:paragraph:cl/1', 'w1:widget:cl/1', 'bt:toggle:cl/1', 'bt1:paragraph:bt/0'].every((x) => f1.includes(x)), f1);
  const look = await p.evaluate(() => { const c = document.querySelector('[data-block-id="cl"] .pm-cols'); return { t: c.style.getPropertyValue('--cols-t'), n: c.style.getPropertyValue('--cols'),
    styleAttr: [...document.querySelectorAll('#pg-editor [style]')].filter((e) => /--cols|grid/.test(e.getAttribute('style'))).length, cols: getComputedStyle(c).gridTemplateColumns.split(' ').length }; });
  ok('Spalten: Breiten ueber CSSOM (--cols-t 0,6/0,4), zwei Spalten nebeneinander', /0\.6fr.*0\.4fr/.test(look.t) && look.n === '2' && look.cols === 2, look);
  ok('Daten-Werkzeug (Widget) in der Spalte als Werkzeug-Ansicht, Code als Code-Block (T4c)', !!(await p.$('[data-block-id="cl"] .hk-tool-widget')) && !!(await p.$('[data-block-id="cl"] [data-block-id="c1"].pm-b-code')));
  const same1 = await p.evaluate(() => HKPages.k.editor().save().then((o) => JSON.stringify(o.blocks)));
  ok('Spalten: save() ohne Bearbeitung byte-gleich mit GET', same1 === J(o1), [same1.slice(0, 200)]);
  await sleep(1200);
  ok('Spalten: Laden schickt kein PATCH', p.patches === 0, p.patches);
  // Tippen in Spalte 2, Liste in Spalte 1
  await D.focus(p, 'b1', 'end'); await p.keyboard.type(' mehr', { delay: 10 });
  await p.evaluate(() => { const ed = HKPages.k.editor().editor(); let pos = null; ed.state.doc.descendants((n, q) => { if (pos == null && n.type.name === 'ltext') pos = q + 1 + n.content.size; }); ed.view.dispatch(ed.state.tr.setSelection(SagaPM.pm.state.TextSelection.create(ed.state.doc, pos))); ed.view.focus(); });
  await p.keyboard.press('Enter'); await p.keyboard.type('zwei', { delay: 10 });
  let b = await saved(P1), cl = find(b, 'cl'), cl0 = find(o1, 'cl');
  ok('Spalten: Tippen in Spalte 2 und Liste in Spalte 1 gespeichert', find(cl.data.cols[1].blocks, 'b1').data.text === 'Rechts mehr' && find(cl.data.cols[0].blocks, 'al').data.items.length === 2, cl.data.cols);
  ok('Spalten: unveraenderte Kinder byte-gleich (a1, a2, w1, c1, Toggle), Breiten wie vorher', J(cl.data.cols[0].blocks.slice(0, 2)) === J(cl0.data.cols[0].blocks.slice(0, 2)) &&
    J(cl.data.cols[1].blocks.slice(1)) === J(cl0.data.cols[1].blocks.slice(1)) && cl.data.cols[0].w === 0.6 && cl.data.cols[1].w === 0.4, cl.data.cols);
  ok('Spalten: Bloecke ausserhalb byte-gleich', J(b.filter((x) => x.id !== 'cl')) === J(o1.filter((x) => x.id !== 'cl')));
  ok('Spalten: Speichern -> GET identisch mit Editorstand', (await p.evaluate(() => HKPages.k.editor().save().then((o) => JSON.stringify(o.blocks)))) === J(b));
  // Breite ziehen (5-%-Raster, ein Zug = ein Undo-Schritt), Doppelklick = gleich
  await hoverBlock('a1');
  const rs = await p.evaluate(() => { const r = document.querySelector('[data-block-id="cl"] .pm-col-rs').getBoundingClientRect(); return [r.left + r.width / 2, r.top + 20]; });
  p.patches = 0;
  await p.mouse.move(rs[0], rs[1]); await p.mouse.down(); await p.mouse.move(rs[0] - 130, rs[1], { steps: 8 }); await p.mouse.up();
  b = await saved(P1); const ws = find(b, 'cl').data.cols.map((c) => c.w);
  ok('Spalten-Ziehen: neue Breiten im 5-%-Raster, Summe 1', ws.every((w) => Math.abs(w * 20 - Math.round(w * 20)) < 1e-9) && Math.abs(ws[0] + ws[1] - 1) < 1e-9 && ws[0] < 0.6, ws);
  ok('Spalten-Ziehen: ein PATCH (eine Transaktion)', p.patches === 1, p.patches);
  await p.keyboard.press(MOD + '+z');
  b = await saved(P1);
  ok('Spalten-Ziehen: Undo stellt beide Breiten in einem Schritt her', J(find(b, 'cl').data.cols.map((c) => c.w)) === J([0.6, 0.4]), find(b, 'cl').data.cols.map((c) => c.w));
  await hoverBlock('a1'); await p.dblclick('[data-block-id="cl"] .pm-col-rs');
  b = await saved(P1);
  ok('Spalten: Doppelklick auf den Griff = gleich breit (kein w)', find(b, 'cl').data.cols.every((c) => !('w' in c)), find(b, 'cl').data.cols.map((c) => c.w));
  // Anzahl 2-6
  await hoverBlock('a1'); await p.click('[data-block-id="cl"] .pm-cols-ctl .cols-ctl-b:text-is("4")');
  b = await saved(P1);
  ok('Spalten: Anzahl 4 (neue Spalten mit leerem Absatz)', find(b, 'cl').data.cols.length === 4 && find(b, 'cl').data.cols[3].blocks[0].type === 'paragraph', colIds(find(b, 'cl')));
  await hoverBlock('a1'); await p.click('[data-block-id="cl"] .pm-cols-ctl .cols-ctl-b:text-is("2")');
  b = await saved(P1);
  ok('Spalten: zurueck auf 2 (leere Absaetze entfernter Spalten fallen weg, Inhalt bleibt)', J(colIds(find(b, 'cl'))) === J([['a1', 'a2', 'al'], ['b1', 'w1', 'c1', 'bt']]), colIds(find(b, 'cl')));
  // "+ Block" je Spalte
  await hoverBlock('b1'); await p.click('[data-block-id="cl"] .pm-col:nth-child(1) .col-add'); await p.keyboard.type('neu links', { delay: 10 });
  b = await saved(P1); const nl = find(b, 'cl').data.cols[0].blocks.slice(-1)[0];
  ok('Spalten: "+ Block" legt am Ende genau dieser Spalte an', nl.type === 'paragraph' && nl.data.text === 'neu links', nl);
  // Block darunter -> Spalte 2 (Leiste)
  await hoverBlock('a1'); await p.hover('[data-block-id="cl"] .pm-cols-ctl'); await sleep(150);
  await p.click('[data-block-id="cl"] .pm-cols-ctl .cols-ctl-x .cols-ctl-b:text-is("2")');
  b = await saved(P1);
  ok('Spalten: "Block darunter -> Spalte 2" (Leiste)', !find(b, 'p9') && find(b, 'cl').data.cols[1].blocks.slice(-1)[0].id === 'p9', ids(b));
  // Verschieben ueber Spaltengrenzen (Cmd+Umschalt+Pfeil) + Undo/Redo als ein Schritt
  await D.focus(p, 'al', 'end'); await p.keyboard.press(MOD + '+Shift+ArrowDown'); await sleep(700);   // hinter "neu links"
  await p.keyboard.press(MOD + '+Shift+ArrowDown');
  b = await saved(P1);
  ok('Cmd+Umschalt+Pfeil: Block wandert ueber die Spaltengrenze (Anfang Spalte 2)', find(b, 'cl').data.cols[1].blocks[0].id === 'al', colIds(find(b, 'cl')));
  await p.keyboard.press(MOD + '+z'); b = await saved(P1, 1700);
  const undone = colIds(find(b, 'cl'));
  await p.keyboard.press(MOD + '+Shift+z'); b = await saved(P1, 1700);
  ok('Undo/Redo ueber Spaltengrenze je ein Schritt', undone[1][0] !== 'al' && undone[0].includes('al') && find(b, 'cl').data.cols[1].blocks[0].id === 'al', [undone, colIds(find(b, 'cl'))]);
  // Auflösen: Spalte 1 leeren -> eine Spalte uebrig -> Spalten-Block aufgeloest, IDs bleiben
  const P1b = await mk('TS Aufloesen', [{ id: 'z0', type: 'paragraph', data: { text: 'vor' } }, { id: 'zc', type: 'columns', data: { cols: [{ blocks: [{ id: 'z1', type: 'paragraph', data: { text: 'eins' } }] }, { blocks: [{ id: 'z2', type: 'paragraph', data: { text: 'zwei' } }, { id: 'z3', type: 'paragraph', data: { text: 'drei' } }] }] } }]);
  await open(P1b);
  await D.focus(p, 'z1', 'end'); await p.keyboard.press(MOD + '+Shift+ArrowUp');
  b = await saved(P1b);
  ok('Leere Spalte entfaellt, eine Spalte uebrig -> aufgeloest (IDs bleiben)', J(ids(b)) === J(['z0', 'z1', 'z2', 'z3']) && !b.some((x) => x.type === 'columns'), ids(b));
  await p.keyboard.press(MOD + '+z');
  b = await saved(P1b);
  ok('Undo stellt Spalten-Block samt Inhalt in einem Schritt wieder her', J(colIds(find(b, 'zc'))) === J([['z1'], ['z2', 'z3']]), ids(b));
  // Slash "/2 Spalten" im leeren Absatz, Verschachtelungsverbot
  const P1c = await mk('TS Slash Spalten', [{ id: 's0', type: 'paragraph', data: { text: '' } }]);
  await open(P1c);
  await D.focus(p, 's0', 'end'); await p.keyboard.type('/2 Spalten', { delay: 15 }); await sleep(300); await p.keyboard.press('Enter'); await sleep(200);
  await p.keyboard.type('links', { delay: 10 });
  await p.keyboard.press('Enter'); await p.keyboard.type('/3 Spalten', { delay: 15 }); await sleep(300); await p.keyboard.press('Enter');
  b = await saved(P1c);
  ok('Slash "2 Spalten": leerer Absatz wird Spalte 1 (ID bleibt), Schreibmarke darin', b[0].type === 'columns' && b[0].data.cols.length === 2 && b[0].data.cols[0].blocks[0].id === 's0' && b[0].data.cols[0].blocks[0].data.text === 'links', b);
  ok('Verschachtelungsverbot: Spalten aus einer Spalte heraus landen unter dem Spalten-Block', b.length === 2 && b[1].type === 'columns' && b[1].data.cols.length === 3 && !b[0].data.cols.some((c) => c.blocks.some((x) => x.type === 'columns')), b.map((x) => x.type));
  const nest = await p.evaluate(() => { const ed = HKPages.k.editor().editor(), s = ed.schema; try { s.nodes.column.create(null, [s.nodes.columns.create({ id: 'x' }, [])]).check(); return 'erlaubt'; } catch (e) { return 'verboten'; } });
  ok('Schema verbietet Spalten in Spalten', nest === 'verboten', nest);

  // =================================================================================================================
  // 2) Rand-Drop, Ziehen zwischen Bloecken, in/aus Toggles und Spalten
  const P2 = await mk('TS Ziehen ' + RUN, [
    { id: 'd1', type: 'paragraph', data: { text: 'Eins' } }, { id: 'd2', type: 'paragraph', data: { text: 'Zwei' } }, { id: 'd3', type: 'paragraph', data: { text: 'Drei' } },
    { id: 'dt', type: 'toggle', data: { title: 'Ein Toggle', open: true, blocks: [{ id: 'dt1', type: 'paragraph', data: { text: 'drin' } }] } },
    { id: 'd4', type: 'paragraph', data: { text: 'Vier' } }, { id: 'd5', type: 'paragraph', data: { text: 'Fuenf' } }]);
  await open(P2);
  await hoverBlock('d2');
  const hs = await p.evaluate(() => {
    const h = document.querySelector('#pg-editor .pm-handles'), m = h.querySelector('.pm-handle-menu').getBoundingClientRect(), pl = h.querySelector('.pm-handle-plus').getBoundingClientRect();
    const t = document.querySelector('[data-block-id="d2"] .pm-in'), r = t.getBoundingClientRect(), lh = parseFloat(getComputedStyle(t).lineHeight);
    return { on: h.classList.contains('pm-handles-on'), w: m.width, hh: m.height, pw: pl.width, mid: m.top + m.height / 2, line: r.top + lh / 2, left: m.right <= r.left + 1,
      tr: getComputedStyle(h).transitionDuration };
  });
  ok('Griffe (E2): + und ⋮⋮ 24x24, links vom Block, mittig zur ersten Zeile, 120-150 ms Einblendung', hs.on && hs.w === 24 && hs.hh === 24 && hs.pw === 24 && Math.abs(hs.mid - hs.line) < 3 && hs.left && /^0\.1[2-5]s$/.test(hs.tr), hs);
  await p.click('#pg-editor .pm-handle-plus'); await sleep(300);
  ok('"+" legt einen Absatz darunter an und oeffnet das Slash-Menue', !!(await p.$(D.UI.popover)) && (await D.blocks(p))[2].type === 'paragraph' && (await D.blocks(p))[2].text === '/', await D.blocks(p));
  await p.keyboard.press('Escape'); await p.keyboard.press('Backspace'); await p.keyboard.press('Backspace'); await sleep(300);
  let st0 = await get(P2), r0 = await rect('d4');
  p.patches = 0;
  let dr = await dragTo('d1', r0.l + r0.w / 2, r0.b - 3);
  b = await saved(P2);
  ok('Ziehen (E3): Geisterbild + waagrechte Ziellinie 2 px', dr.ghost && dr.line && !dr.v && Math.round(dr.h) === 2, dr);
  ok('Ziehen zwischen Bloecken: d1 hinter d4, ein PATCH', J(ids(b)) === J(['d2', 'd3', 'dt', 'd4', 'd1', 'd5']) && p.patches === 1, [ids(b), p.patches]);
  ok('Ziehen: Block unveraendert (byte-gleich) an neuer Stelle', J(find(b, 'd1')) === J(find(st0, 'd1')));
  await p.keyboard.press(MOD + '+z'); b = await saved(P2, 1700);
  ok('Ziehen: Undo in einem Schritt', J(ids(b).slice(0, 2)) === J(['d1', 'd2']) || J(ids(b)) === J(ids(st0)), ids(b));
  // in den Toggle (untere Haelfte des Titels) und wieder heraus
  r0 = await rect('dt', '[data-pm-part="ttitle"]');
  await dragTo('d2', r0.l + 40, r0.b - 3);
  b = await saved(P2);
  ok('Ziehen in einen offenen Toggle (unter den Titel) -> erster Inhalt', J(find(b, 'dt').data.blocks.map((x) => x.id)) === J(['d2', 'dt1']), find(b, 'dt').data.blocks);
  r0 = await rect('d5');
  await dragTo('dt1', r0.l + r0.w / 2, r0.b - 3);
  b = await saved(P2);
  ok('Ziehen aus dem Toggle heraus', ids(b).slice(-1)[0] === 'dt1' && J(find(b, 'dt').data.blocks.map((x) => x.id)) === J(['d2']), ids(b));
  // Rand-Drop: rechts an d3 -> neue Spalten [d3 | d4]
  r0 = await rect('d3');
  dr = await dragTo('d4', r0.r - 12, r0.t + r0.h / 2);
  b = await saved(P2);
  const nc = b.find((x) => x.type === 'columns');
  ok('Rand-Drop: senkrechte Ziellinie 2 px', dr.line && dr.v && Math.round(dr.w) === 2, dr);
  ok('Rand-Drop rechts an einem Block der obersten Ebene -> neue Spalten (d3 | d4)', nc && J(colIds(nc)) === J([['d3'], ['d4']]), ids(b));
  r0 = await rect(nc.id);
  await dragTo('d5', r0.l + 8, r0.t + 10);
  b = await saved(P2);
  ok('Rand-Drop am linken Rand von Spalten -> neue erste Spalte, Breiten gleich', J(colIds(find(b, nc.id))) === J([['d5'], ['d3'], ['d4']]) && find(b, nc.id).data.cols.every((c) => !('w' in c)), colIds(find(b, nc.id)));
  // aus der Spalte ziehen bis eine uebrig ist -> aufgeloest
  r0 = await rect('d1');
  await dragTo('d5', r0.l + r0.w / 2, r0.t + 3); await sleep(400);
  r0 = await rect('d1');
  await dragTo('d4', r0.l + r0.w / 2, r0.t + 3);
  b = await saved(P2);
  ok('Letzte Bloecke aus Spalten gezogen -> Spalten aufgeloest (d3 bleibt mit ID)', !b.some((x) => x.type === 'columns') && ids(b).includes('d3') && ids(b).indexOf('d5') < ids(b).indexOf('d1'), ids(b));
  const plen = ids(b).length;
  // Spalten in eine Spalte ziehen geht nicht (Rand-Drop bietet sich nicht an)
  const P2b = await mk('TS Ziehen Spalten', [{ id: 'e1', type: 'columns', data: { cols: [{ blocks: [{ id: 'e2', type: 'paragraph', data: { text: 'L' } }] }, { blocks: [{ id: 'e3', type: 'paragraph', data: { text: 'R' } }] }] } },
    { id: 'e4', type: 'columns', data: { cols: [{ blocks: [{ id: 'e5', type: 'paragraph', data: { text: 'L2' } }] }, { blocks: [{ id: 'e6', type: 'paragraph', data: { text: 'R2' } }] }] } }]);
  await open(P2b);
  const eb = await get(P2b);
  await hoverBlock('e4'); await p.mouse.move((await rect('e4')).l + 4, (await rect('e4')).t + 4); await sleep(250);
  const hid = await p.evaluate(() => { const h = document.querySelector('.pm-handles'); return h && h.classList.contains('pm-handles-on'); });
  if (hid) { r0 = await rect('e2'); await dragTo('e4', r0.l + r0.w / 2, r0.b - 2, ':scope > .pm-cols'); }
  b = await saved(P2b);
  ok('Spalten lassen sich nicht in eine Spalte ziehen (Schema)', !b[0].data.cols.some((c) => c.blocks.some((x) => x.type === 'columns')) && b.length >= 1, b.map((x) => x.type));
  void eb; void plen;

  // =================================================================================================================
  // 3) Toggle: Laden, auf/zu (T-E6 A), Markdown, Titel-Tasten, Umwandeln, Altformat
  const TG = [
    { id: 'g1', type: 'toggle', data: { title: 'Auf', open: true, blocks: [{ id: 'g1a', type: 'paragraph', data: { text: 'innen' } }] } },
    { id: 'g2', type: 'toggle', data: { title: 'Zu <i>kursiv</i>', open: false, level: 2, blocks: [{ id: 'g2a', type: 'paragraph', data: { text: 'versteckt' } }] } },
    { id: 'g3', type: 'toggle', data: { text: 'Altformat', open: false } },
    { id: 'g9', type: 'paragraph', data: { text: 'Ende' } }];
  const P3 = await mk('TS Toggle ' + RUN, TG);
  const o3 = await get(P3);
  await open(P3);
  const tv = await p.evaluate(() => ({ cards: document.querySelectorAll('#pg-editor .pm-card').length, g2open: document.querySelector('[data-block-id="g2"]').classList.contains('open'),
    g2aVis: !!document.querySelector('[data-block-id="g2a"]').offsetParent, g2aInDoc: !!HKPages.k.doc.byId('g2a'), h2: document.querySelector('[data-block-id="g2"]').classList.contains('pm-tgl-h2'),
    alt: (HKPages.k.doc.blocks().find((b) => b.parent === 'g3') || {}).el && HKPages.k.doc.blocks().find((b) => b.parent === 'g3').el.textContent }));
  ok('Toggle: echter Knoten; zugeklappt per CSS verborgen, bleibt im Dokument (Fassade)', tv.cards === 0 && !tv.g2open && !tv.g2aVis && tv.g2aInDoc, tv);
  ok('Toggle-Ueberschrift 2 dargestellt; Altformat {text} als erster Absatz', tv.h2 && tv.alt === 'Altformat', tv);
  ok('Toggle: save() ohne Bearbeitung byte-gleich (auch Altformat)', (await p.evaluate(() => HKPages.k.editor().save().then((o) => JSON.stringify(o.blocks)))) === J(o3));
  await sleep(1200);
  ok('Toggle: Laden (auch Altformat) schickt kein PATCH', p.patches === 0, p.patches);
  await p.click('[data-block-id="g2"] > .pm-tgl-btn');
  b = await saved(P3);
  ok('Toggle aufklappen (eigener Klick) schreibt open:true, sonst alles byte-gleich', find(b, 'g2').data.open === true && J(b.filter((x) => x.id !== 'g2')) === J(o3.filter((x) => x.id !== 'g2')) &&
    J(find(b, 'g2').data.blocks) === J(find(o3, 'g2').data.blocks), find(b, 'g2'));
  ok('Toggle aufgeklappt: Inhalt sichtbar', await p.evaluate(() => !!document.querySelector('[data-block-id="g2a"]').offsetParent));
  await p.keyboard.press(MOD + '+z'); b = await saved(P3, 1700);
  ok('Auf/zu ist kein Undo-Schritt (bleibt offen)', find(b, 'g2').data.open === true, find(b, 'g2').data.open);
  // Markdown "> " -> Toggle, Enter im Titel -> Inhalt, Enter auf leerem letzten Absatz -> hinter den Toggle; '" ' -> Zitat
  await D.focus(p, 'g9', 'end'); await p.keyboard.press('Enter'); await p.keyboard.type('> Neu', { delay: 15 });
  await p.keyboard.press('Enter'); await p.keyboard.type('drin', { delay: 15 }); await p.keyboard.press('Enter'); await p.keyboard.press('Enter');
  await p.keyboard.type('danach', { delay: 15 }); await p.keyboard.press('Enter'); await p.keyboard.type('" Zitat', { delay: 15 });
  b = await saved(P3);
  const tail = b.slice(-3);
  ok('Markdown "> " -> Toggle (E5), Enter im Titel -> erster Inhalt, Enter auf leerem letzten -> hinter den Toggle', tail[0].type === 'toggle' && tail[0].data.title === 'Neu' && tail[0].data.open === true &&
    J(tail[0].data.blocks.map((x) => x.data.text)) === J(['drin']) && tail[1].data.text === 'danach', tail);
  ok("Markdown '\" ' -> Zitat (E5)", tail[2].type === 'quote' && tail[2].data.text === 'Zitat', tail[2]);
  // Slash: Toggle-Ueberschrift 3
  await p.keyboard.press('Enter'); await p.keyboard.press('Enter'); await p.keyboard.type('/Toggle-Überschrift 3', { delay: 10 }); await sleep(300); await p.keyboard.press('Enter'); await p.keyboard.type('T3', { delay: 10 });
  b = await saved(P3);
  ok('Slash "Toggle-Überschrift 3" -> toggle level 3', b.some((x) => x.type === 'toggle' && x.data.level === 3 && x.data.title === 'T3'), b.slice(-2));
  // Umwandeln mit Inhalt verweigern (Block-Menue), ohne Inhalt erlaubt
  await hoverBlock('g1', '[data-pm-part="ttitle"]'); await p.click('.pm-handle-menu'); await sleep(200);
  await p.hover('.menu .menu-item:has-text("Umwandeln in")'); await sleep(200); await p.click('.menu-sub .menu-item:has-text("Text")'); await sleep(300);
  ok('Toggle mit Inhalt: Umwandeln verweigert (Hinweis)', /Inhalt/.test(await p.textContent('.toast')) && (await get(P3)).find((x) => x.id === 'g1').type === 'toggle');
  // Ruecktaste am Titelanfang: Toggle -> Absatz, Inhalt rueckt dahinter (IDs bleiben)
  await D.focus(p, 'g1', 'start'); await p.keyboard.press('Backspace');
  b = await saved(P3);
  ok('Ruecktaste am Titelanfang: Toggle wird Absatz (ID bleibt), Inhalt dahinter', b[0].id === 'g1' && b[0].type === 'paragraph' && b[0].data.text === 'Auf' && b[1].id === 'g1a', ids(b).slice(0, 3));
  await p.keyboard.press(MOD + '+z'); b = await saved(P3, 1700);
  ok('Undo: Toggle wieder da (ein Schritt)', b[0].type === 'toggle' && J(b[0].data.blocks.map((x) => x.id)) === J(['g1a']), b[0]);
  // Altformat bearbeiten -> neues Format
  const g3a = await p.evaluate(() => HKPages.k.doc.blocks().find((b) => b.parent === 'g3').id);
  await p.click('[data-block-id="g3"] > .pm-tgl-btn'); await D.focus(p, g3a, 'end'); await p.keyboard.type(' neu', { delay: 10 });
  b = await saved(P3);
  ok('Altformat nach Bearbeitung im neuen Format {title, blocks}', !('text' in find(b, 'g3').data) && find(b, 'g3').data.blocks[0].data.text === 'Altformat neu', find(b, 'g3'));

  // =================================================================================================================
  // 4) Rollen: Lesen (Toggle lokal auf/zu, keine Griffe, kein PATCH), gesperrt, fremde Seite
  const P4 = await mk('TS Rollen ' + RUN, [{ id: 'r1', type: 'toggle', data: { title: 'Lesen', open: false, blocks: [{ id: 'r2', type: 'paragraph', data: { text: 'x' } }] } },
    { id: 'r3', type: 'columns', data: { cols: [{ blocks: [{ id: 'r4', type: 'paragraph', data: { text: 'L' } }] }, { blocks: [{ id: 'r5', type: 'paragraph', data: { text: 'R' } }] }] } }]);
  if (CK2 && HANDLE2) {
    await p.api('PUT', '/api/notes/' + P4 + '/shares', { type: 'user', target: HANDLE2, role: 'view' });
    const B = await session(CK2); await D.setEditor(B.p, 'tiptap'); await open(P4, B.p);
    await B.p.click('[data-block-id="r1"] > .pm-tgl-btn'); await sleep(200);
    const rv = await B.p.evaluate(() => ({ open: document.querySelector('[data-block-id="r1"]').classList.contains('open'), vis: !!document.querySelector('[data-block-id="r2"]').offsetParent,
      ctl: [...document.querySelectorAll('.pm-cols-ctl, .pm-col-rs, .col-add')].filter((e) => e.offsetParent).length }));
    await B.p.mouse.move(400, 300); await B.p.hover('[data-block-id="r4"]'); await sleep(300);
    const rh = await B.p.evaluate(() => { const h = document.querySelector('.pm-handles'); return !!h && h.classList.contains('pm-handles-on') && !!h.offsetParent; });
    await sleep(1300);
    ok('Rolle Lesen: Toggle klappt lokal auf, kein PATCH, keine Spalten-Bedienelemente, keine Griffe', rv.open && rv.vis && rv.ctl === 0 && !rh && B.p.patches === 0 && find(await get(P4), 'r1').data.open === false, [rv, rh, B.p.patches]);
    ok('Rolle Lesen: keine Seitenfehler', !B.p.errs.length, B.p.errs);
    await B.ctx.close();
  }
  await p.api('PATCH', '/api/notes/' + P4, { locked: true });
  await open(P4);
  await p.click('[data-block-id="r1"] > .pm-tgl-btn'); await sleep(1300);
  ok('Gesperrt: Toggle nur lokal, nichts gespeichert', p.patches === 0 && find(await get(P4), 'r1').data.open === false, p.patches);
  await p.api('PATCH', '/api/notes/' + P4, { locked: false });

  // =================================================================================================================
  // 5) Block-Menue (⋮⋮ und Cmd+/)
  const P5 = await mk('TS Menue ' + RUN, [{ id: 'm1', type: 'paragraph', data: { text: 'eins' } }, { id: 'm2', type: 'list', data: { style: 'ordered', meta: {}, items: [{ content: 'a', meta: {}, items: [] }] } },
    { id: 'm3', type: 'quote', data: { text: 'q', caption: '', alignment: 'left' } }, { id: 'm4', type: 'paragraph', data: { text: 'vier' } }]);
  await open(P5);
  await hoverBlock('m1'); await p.click('.pm-handle-menu'); await sleep(200);
  const items = await p.evaluate(() => [...document.querySelectorAll('.menu:not([hidden]) .menu-item .mi-t')].map((x) => x.textContent));
  ok('Block-Menue (⋮⋮): Umwandeln, Farbe, Duplizieren, Verschieben nach …, synchronisiert kopieren, Link, Kommentieren, Löschen',
    ['Umwandeln in', 'Farbe', 'Duplizieren', 'Verschieben nach …', 'Als synchronisierten Block kopieren', 'Link zum Block kopieren', 'Löschen'].every((t) => items.includes(t)) && !!(await p.$(D.UI.blockMenu)), items);
  await p.click('.menu .menu-item:has-text("Duplizieren")');
  b = await saved(P5);
  ok('Block-Menue: Duplizieren (neue ID, gleicher Inhalt)', b.length === 5 && b[1].data.text === 'eins' && b[1].id !== 'm1', ids(b));
  await D.focus(p, 'm2', 'end'); await p.keyboard.press(MOD + '+/'); await sleep(200);
  const it2 = await p.evaluate(() => [...document.querySelectorAll('.menu:not([hidden]) .menu-item .mi-t')].map((x) => x.textContent));
  ok('Cmd+/ oeffnet dasselbe Block-Menue (Liste: Zaehlart, Beginnt mit …)', it2.includes('Zählart') && it2.includes('Beginnt mit …') && it2.includes('Umwandeln in'), it2);
  await p.hover('.menu .menu-item:has-text("Zählart")'); await sleep(200); await p.click('.menu-sub .menu-item:has-text("i, ii, iii")');
  b = await saved(P5);
  ok('Werkzeug-Einstellung Liste: Zaehlart lower-roman', find(b, 'm2').data.meta.counterType === 'lower-roman', find(b, 'm2').data.meta);
  await hoverBlock('m3'); await p.click('.pm-handle-menu'); await sleep(200); await p.click('.menu .menu-item:has-text("Zentriert")');
  b = await saved(P5);
  ok('Werkzeug-Einstellung Zitat: Ausrichtung zentriert', find(b, 'm3').data.alignment === 'center');
  await hoverBlock('m4'); await p.click('.pm-handle-menu'); await sleep(200);
  await p.hover('.menu .menu-item:has-text("Farbe")'); await sleep(200); await p.click('.menu-sub .menu-item:has-text("Blau (Hintergrund)")');
  await hoverBlock('m4'); await p.click('.pm-handle-menu'); await sleep(200);
  await p.hover('.menu .menu-item:has-text("Umwandeln in")'); await sleep(200); await p.click('.menu-sub .menu-item:has-text("Toggle-Überschrift 1")');
  b = await saved(P5);
  ok('Block-Menue: Farbe + Umwandeln in Toggle-Ueberschrift 1 (ID und Farbe bleiben)', find(b, 'm4').type === 'toggle' && find(b, 'm4').data.level === 1 && J(find(b, 'm4').tunes.hkstyle) === J({ c: 'blue', bg: true }), find(b, 'm4'));
  await hoverBlock('m1'); await p.click('.pm-handle-menu'); await sleep(200); await p.click('.menu .menu-item:has-text("Löschen")');
  b = await saved(P5);
  ok('Block-Menue: Loeschen', !find(b, 'm1'), ids(b));

  // =================================================================================================================
  // 6) Mehrfachauswahl ueber Ebenen und Spalten (T-E5 A, E4)
  const P6 = await mk('TS Auswahl ' + RUN, [{ id: 'k0', type: 'paragraph', data: { text: 'oben' } },
    { id: 'kc', type: 'columns', data: { cols: [{ blocks: [{ id: 'k1', type: 'paragraph', data: { text: 'L1' } }, { id: 'k2', type: 'paragraph', data: { text: 'L2' } }] },
      { blocks: [{ id: 'k3', type: 'paragraph', data: { text: 'R1' } }, { id: 'k4', type: 'paragraph', data: { text: 'R2' } }] }] } },
    { id: 'k5', type: 'paragraph', data: { text: 'unten' } }, { id: 'k6', type: 'paragraph', data: { text: 'ganz unten' } }]);
  await open(P6);
  await D.focus(p, 'k2', 'end'); await p.keyboard.press('Escape'); await sleep(100);
  ok('Esc markiert den aktuellen Block', J(await sel()) === J(['k2']), await sel());
  await p.keyboard.press('Shift+ArrowDown'); await sleep(150);
  ok('Umschalt+Pfeil erweitert ueber die Spaltengrenze (k2 -> k3)', J(await sel()) === J(['k2', 'k3']), await sel());
  const barOn = await p.evaluate(() => { const b = document.querySelector('.hk-sel-bar'); return !!b && !b.hidden && /2 Bl/.test(b.textContent); });
  ok('Leiste wie heute bei 2 Bloecken', barOn);
  await p.keyboard.press('Backspace');
  b = await saved(P6);
  ok('Rueckschritt loescht die Auswahl ueber beide Spalten (eine Transaktion)', J(colIds(find(b, 'kc'))) === J([['k1'], ['k4']]), colIds(find(b, 'kc')));
  await p.keyboard.press(MOD + '+z'); b = await saved(P6, 1700);
  ok('Undo stellt beide Bloecke in einem Schritt her', J(colIds(find(b, 'kc'))) === J([['k1', 'k2'], ['k3', 'k4']]), colIds(find(b, 'kc')));
  // Text-Auswahl ueber Blockgrenzen -> Blockauswahl
  const ra = await rect('k5', '.pm-in'), rb = await rect('k6', '.pm-in');
  await p.mouse.move(ra.l + 4, ra.t + 8); await p.mouse.down(); await p.mouse.move(rb.l + 30, rb.t + 8, { steps: 8 }); await p.mouse.up(); await sleep(300);
  ok('Text-Auswahl ueber Blockgrenzen wird Blockauswahl', J(await sel()) === J(['k5', 'k6']), await sel());
  // Cmd+A zweimal
  await D.focus(p, 'k0', 'end'); await p.keyboard.press(MOD + '+a'); await sleep(100);
  const a1 = { s: await sel(), t: await p.evaluate(() => getSelection().toString()) };
  await p.keyboard.press(MOD + '+a'); await sleep(100);
  const all = await sel();
  ok('Cmd+A: erst Text des Blocks, zweimal = alle Bloecke (ueber Ebenen)', a1.s.length === 0 && a1.t === 'oben' && ['k0', 'kc', 'k1', 'k4', 'k6'].every((x) => all.includes(x)), [a1, all]);
  await p.keyboard.press('Escape');
  // Leiste "Umwandeln" ueber Ebenen: je zusammenhaengender Gruppe
  await D.select(p, ['k0', 'k1', 'k3']); await sleep(150);
  await p.click('.hk-sel-bar [data-a="convert"]'); await sleep(150); await p.click('.menu .menu-item:has-text("Überschrift 2")');
  b = await saved(P6);
  ok('Leiste: Umwandeln ueber Ebenen je Gruppe (oben + Spalte 1 + Spalte 2)', b[0].type === 'header' && find(b, 'kc').data.cols[0].blocks[0].type === 'header' && find(b, 'kc').data.cols[1].blocks[0].type === 'header' &&
    find(b, 'kc').data.cols[0].blocks[0].id === 'k1', [b[0], colIds(find(b, 'kc'))]);
  // Cmd+Umschalt+Pfeil verschiebt die Gruppe
  await D.select(p, ['k5', 'k6']); await p.evaluate(() => { const v = HKPages.k.editor().editor().view; v.focus(); });
  await p.keyboard.press(MOD + '+Shift+ArrowUp');
  b = await saved(P6);
  ok('Cmd+Umschalt+Pfeil verschiebt die gewaehlte Gruppe', J(ids(b)) === J(['k0', 'k5', 'k6', 'kc']), ids(b));
  // Ziehen im Rand
  const rr = await rect('k0'), rk = await rect('k5');
  await p.mouse.move(rr.l - 30, rr.t + 4); await p.mouse.down(); await p.mouse.move(rr.l + 120, rk.b - 4, { steps: 8 }); await p.mouse.up(); await sleep(200);
  ok('Ziehen im Rand waehlt die ueberstrichenen Bloecke', J(await sel()) === J(['k0', 'k5']), await sel());
  // Mehrfach ziehen in eine Spalte
  const rk3 = await rect('k3');
  await dragTo('k5', rk3.l + rk3.w / 2, rk3.b - 3);
  b = await saved(P6);
  ok('Mehrere gewaehlte Bloecke zusammen in eine Spalte gezogen', J(find(b, 'kc').data.cols[1].blocks.map((x) => x.id)) === J(['k3', 'k0', 'k5', 'k4']), colIds(find(b, 'kc')));
  // Kopieren/Einfuegen der Auswahl (gleiche Seite)
  await D.select(p, ['k1', 'k2']);
  await p.evaluate(() => HKPages.k.editor().editor().view.focus());
  const clip = await p.evaluate(() => new Promise((res) => {
    const dt = new DataTransfer(), e = new ClipboardEvent('copy', { clipboardData: dt, bubbles: true, cancelable: true });
    HKPages.k.editor().editor().view.dom.dispatchEvent(e); res({ html: dt.getData('text/html'), json: dt.getData('application/x-editor-js'), txt: dt.getData('text/plain') });
  }));
  ok('Kopieren der Blockauswahl: HTML (Schema), Saga-JSON, Text', /data-block-id="k1"/.test(clip.html) && JSON.parse(clip.json || '[]').length === 2 && /L2/.test(clip.txt), clip);
  await D.focus(p, 'k6', 'end');
  await p.evaluate((html) => { const dt = new DataTransfer(); dt.setData('text/html', html); HKPages.k.editor().editor().view.dom.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }, clip.html);
  b = await saved(P6);
  const pasted = b.slice(b.findIndex((x) => x.id === 'k6') + 1).slice(0, 2);
  ok('Einfuegen der kopierten Bloecke: neue IDs, gleicher Inhalt', pasted.length >= 2 && pasted.every((x) => !['k1', 'k2'].includes(x.id)) && pasted.map((x) => x.data.text).join() === 'L1,L2', b.map((x) => x.id + ':' + (x.data.text || '')));

  // =================================================================================================================
  // 7) Synchronisierte Bloecke: Original (Block-Menue), Kopie auf anderer Seite, Bearbeiten der Kopie (sync1), Zustaende
  const P7 = await mk('TS Sync ' + RUN, [{ id: 'y1', type: 'paragraph', data: { text: 'geteilt' } }, { id: 'y2', type: 'paragraph', data: { text: 'Rest' } }]);
  await open(P7);
  await hoverBlock('y1'); await p.click('.pm-handle-menu'); await sleep(200); await p.click('.menu .menu-item:has-text("Als synchronisierten Block kopieren")');
  b = await saved(P7, 2200);
  const orig = b.find((x) => x.type === 'synced');
  ok('Als synchronisierten Block kopieren: Original {sid, blocks:[y1]} gespeichert', orig && /^[A-Za-z0-9]{16}$/.test(orig.data.sid) && orig.data.blocks[0].id === 'y1', b);
  ok('Original: Leiste "Original" (Container bearbeitbar)', /Original/.test(await p.textContent('[data-block-type="synced"] .pm-sync-head')));
  const P7b = await mk('TS Sync Kopie ' + RUN, [{ id: 'q1', type: 'paragraph', data: { text: 'vor' } }]);
  await open(P7b);
  await p.evaluate((sid) => HKPages.k.doc.insertAfter('q1', [{ type: 'synced', data: { sid } }]), orig.data.sid);
  ok('Frisch eingefuegte Kopie (synced_ref): "Inhalt erscheint nach dem Speichern"', /nach dem Speichern/.test(await p.textContent('[data-block-type="synced"]')));
  await sleep(3500);
  const cv = await p.evaluate(() => { const s = document.querySelector('[data-block-type="synced"]'); return s && { cls: s.className, t: s.textContent }; });
  ok('Nach dem Speichern: Kopie mit Inhalt des Originals ("Synchronisiert", "Original öffnen")', cv && /copy/.test(cv.cls) && /geteilt/.test(cv.t) && /Original öffnen/.test(cv.t), cv);
  b = await get(P7b);
  ok('Kopie gespeichert als {sid} (Server setzt Inhalt nur beim Lesen ein)', b[1].type === 'synced' && b[1].data.sid === orig.data.sid, b[1]);
  await D.focus(p, 'y1', 'end'); await p.keyboard.type(' (Kopie)', { delay: 15 }); await sleep(2500);
  b = await get(P7);
  ok('Bearbeiten in der Kopie schreibt ins Original durch (sync1, Stufe 1)', b.find((x) => x.type === 'synced').data.blocks[0].data.text === 'geteilt (Kopie)', b);
  const P7c = await mk('TS Sync fehlt', [{ id: 'v1', type: 'synced', data: { sid: 'AbCdEfGhIjKlMnOp' } }]);
  await open(P7c);
  ok('Kopie mit unbekannter sid: "nicht gefunden", kein PATCH', /nicht gefunden/.test(await p.textContent('[data-block-type="synced"]')) && p.patches === 0);
  if (CK2 && HANDLE2) {
    await p.api('PUT', '/api/notes/' + P7 + '/shares', { type: 'user', target: HANDLE2, role: 'view' });
    await p.api('PUT', '/api/notes/' + P7b + '/shares', { type: 'user', target: HANDLE2, role: 'edit' });
    const B = await session(CK2); await D.setEditor(B.p, 'tiptap'); await open(P7b, B.p);
    const lk = await B.p.evaluate(() => { const s = document.querySelector('[data-block-type="synced"]'); return s && { cls: s.className, t: s.textContent }; });
    await B.p.evaluate(() => HKPages.k.doc.focus('y1', 'end')); await B.p.keyboard.type('XX'); await sleep(1800);
    ok('Kopie nur lesend (Lesen auf dem Original): gesperrt, Tippen aendert nichts', lk && /locked/.test(lk.cls) && /nur lesend/.test(lk.t) && !/XX/.test(JSON.stringify(await get(P7))), lk);
    await B.ctx.close();
  }

  // =================================================================================================================
  // 8) Konfliktblock: Uebernehmen = Original ersetzen + Attribut leeren (eine Transaktion), Verwerfen
  const P8 = await mk('TS Konflikt', [{ id: 'x1', type: 'paragraph', data: { text: 'meins' } },
    { id: 'x2', type: 'paragraph', data: { text: 'deins' }, tunes: { hkconflict: { of: 'x1', name: 'Bert' } } },
    { id: 'x3', type: 'paragraph', data: { text: 'alt' } }, { id: 'x4', type: 'paragraph', data: { text: 'neu' }, tunes: { hkconflict: { of: 'x3', name: 'Bert' } } }]);
  await open(P8);
  const cfb = await p.evaluate(() => [...document.querySelectorAll('.pm-cf-bar')].map((x) => x.getAttribute('data-cf-for') + ':' + x.textContent));
  ok('Konfliktblock: Leiste mit Name, Uebernehmen/Verwerfen', cfb.length === 2 && /Bert/.test(cfb[0]) && /Übernehmen/.test(cfb[0]), cfb);
  await sleep(1000); ok('Konfliktblock: Laden ohne PATCH', p.patches === 0, p.patches);
  await p.click('.pm-cf-bar[data-cf-for="x2"] .hk-cf-take');
  b = await saved(P8);
  ok('Uebernehmen: Original weg, Attribut leer', J(ids(b)) === J(['x2', 'x3', 'x4']) && !find(b, 'x2').tunes, b);
  await p.keyboard.press(MOD + '+z'); b = await saved(P8, 1700);
  ok('Uebernehmen ist ein Undo-Schritt', J(ids(b)) === J(['x1', 'x2', 'x3', 'x4']) && find(b, 'x2').tunes.hkconflict.of === 'x1', b);
  await p.click('.pm-cf-bar[data-cf-for="x4"] .hk-cf-drop');
  b = await saved(P8);
  ok('Verwerfen: Konfliktfassung weg, Original bleibt', !find(b, 'x4') && find(b, 'x3').data.text === 'alt', ids(b));

  // =================================================================================================================
  // 8b) Klick neben/zwischen Karten: Textblock unter/ueber Spalten mit Widgets bzw. einem Widget bekommt die
  // Schreibmarke (auch beim Klick in den Abstand), Klick auf die Karte waehlt weiter die Karte
  const WG = (id, kind, cfg) => ({ id, type: 'widget', data: { v: 1, kind, cfg: cfg || {} } });
  const P8b = await mk('TS Klick ' + RUN, [{ id: 'g0', type: 'paragraph', data: { text: 'Davor' } },
    { id: 'gc', type: 'columns', data: { cols: [{ blocks: [WG('gw1', 'clock', { tz: 'UTC', h24: true, sec: true })] }, { blocks: [WG('gw2', 'progress', { src: 'fixed', value: 1, max: 4 })] }] } },
    WG('gw3', 'zukunft'), { id: 'g1', type: 'paragraph', data: { text: 'Danach' } }, { id: 'g2', type: 'paragraph', data: { text: 'Ende' } }]);
  const caretIn = () => p.evaluate(() => { const ed = HKPages.k.editor().editor(), s = ed.state.selection;
    if (s.node) return 'karte:' + s.node.attrs.id; const b = s.$head.node(1); return (s.empty && ed.view.hasFocus() ? 'text:' : 'anders:') + (b && b.attrs.id); });
  const klick = [];
  for (const [lab, where, want] of [
    ['mitten in den Absatz unter der Karte', async () => { const r = await rect('g1'); return [r.l + 30, r.t + r.h / 2]; }, 'text:g1'],
    ['in den Abstand zwischen Karte und Absatz', async () => { const r = await rect('g1'), w = await rect('gw3'); return [r.l + 30, (w.b + r.t) / 2]; }, 'text:g1'],
    ['in den Abstand direkt unter der Karte', async () => { const w = await rect('gw3'); return [w.l + 30, w.b + 1]; }, 'text:g1'],
    ['in den Abstand ueber den Spalten', async () => { const r = await rect('g0'), c = await rect('gc', '.pm-cols'); return [r.l + 30, (r.b + c.t) / 2]; }, 'text:g0'],
    ['auf das Uhr-Widget in der Spalte', async () => { const w = await rect('gw1'); return [w.l + w.w / 2, w.t + w.h / 2]; }, 'karte:gw1'],
    ['auf die Karte unter den Spalten', async () => { const w = await rect('gw3'); return [w.l + 30, w.t + w.h / 2]; }, 'karte:gw3']]) {
    await open(P8b);
    const [x, y] = await where(); await p.mouse.click(x, y); await sleep(200);
    const got = await caretIn(); klick.push(lab + ' -> ' + got);
    ok('Klick ' + lab + ': ' + want, got === want, { got, x, y });
  }
  ok('Klicks neben Karten aendern das Dokument nicht', J(ids(await saved(P8b, 600))) === J(['g0', 'gc', 'gw3', 'g1', 'g2']), klick);
  // Tippen bei ausgewaehlter Karte ersetzt sie nicht (versehentlicher Tastendruck nach Klick ins Widget)
  await open(P8b);
  const vorTippen = J(await saved(P8b, 0));
  { const w = await rect('gw3'); await p.mouse.click(w.l + 30, w.t + w.h / 2); await sleep(200); }
  await p.keyboard.type('xy', { delay: 30 }); await sleep(300);
  const nachTippen = await caretIn();
  ok('Tippen bei gewaehlter Karte: Karte bleibt gewaehlt', nachTippen === 'karte:gw3', nachTippen);
  const nachJ = J(await saved(P8b, 600));
  ok('Tippen bei gewaehlter Karte aendert das Dokument nicht', nachJ === vorTippen && nachJ.indexOf('"gw3"') > 0, nachJ.slice(0, 300));

  // =================================================================================================================
  // 9) Telefon: Spalten untereinander
  const T9 = await session(CK, { width: 390, height: 800 });
  await open(P1, T9.p);
  const ph = await T9.p.evaluate(() => { const c = document.querySelector('[data-block-id="cl"] .pm-cols'); return { cols: getComputedStyle(c).gridTemplateColumns.split(' ').length, rs: [...document.querySelectorAll('.pm-col-rs')].filter((e) => e.offsetParent).length }; });
  ok('Telefon: Spalten untereinander, keine Breitengriffe', ph.cols === 1 && ph.rs === 0, ph);
  await T9.ctx.close();

  // =================================================================================================================
  // 10) Speicher nach Seitenwechseln, CSP, Fehler
  const cdp = await A.ctx.newCDPSession(p);
  await cdp.send('Performance.enable'); await cdp.send('HeapProfiler.enable');
  async function metrics() {
    await cdp.send('HeapProfiler.collectGarbage'); await sleep(150);
    const m = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map((x) => [x.name, x.value]));
    return { kb: Math.round(m.JSHeapUsedSize / 1024), nodes: m.Nodes, lst: m.JSEventListeners };
  }
  async function go(id) { await p.evaluate((id) => { location.hash = '#' + id; }, id); for (let i = 0; i < 100; i++) { if (await p.evaluate((id) => HKPages.k.cur() && HKPages.k.cur().id === id && HKPages.k.doc.blocks().length, id)) break; await sleep(30); } await sleep(250); }
  await go(P1); await go(P3);
  const m0 = await metrics(), N = 10;
  for (let i = 0; i < N; i++) { await go(P1); await go(P3); }
  const m1 = await metrics(), per = { nodes: Math.round((m1.nodes - m0.nodes) / N), lst: Math.round((m1.lst - m0.lst) / N), kb: Math.round((m1.kb - m0.kb) / N) };
  ok('Speicher: kein Zuwachs je Seitenwechsel mit Spalten/Toggles (' + J(per) + ')', per.nodes < 40 && per.lst < 25 && per.kb < 150, { m0, m1 });
  ok('Ein Griff-Satz und ein Editor je Seite', (await p.evaluate(() => [document.querySelectorAll('#pg-editor .ProseMirror').length, document.querySelectorAll('.pm-handles').length].join())) === '1,1');
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
