// Bughunt P01: Seitenwechsel darf keinen Speicher kosten. Editor.js 2.31.7 hielt beim Weiterverwenden des Editors
// (render()) die Listener jedes alten Blocks fest (BlockManager.listeners.allListeners) - und damit das ganze alte
// Editor-DOM: +2 000 Knoten / +1 500 Listener je Oeffnen einer 300-Block-Seite. Dazu blieben innere Editoren
// (Ausklappbar) beim Wechsel am Leben. Prueft Knoten/Listener nach GC (CDP) ueber viele Wechsel und dass der Editor
// danach normal arbeitet (Enter teilt den Block, Markdown-Kuerzel, Ausklappbar speichert, schnelles Wechseln).
// Wie tests/browser_web.js NICHT im Stack-Testlauf, sondern gegen einen laufenden Test-Stack (nur Chromium, CDP):
//   SAGA_BASE=http://host:8175 SAGA_USER=konto SAGA_PASS=... [PLAYWRIGHT=/pfad/zu/playwright-core] [N=12] node tests/browser_editor_leak.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS, N = +(process.env.N || 12);
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function many(n, tag) {
  const b = [];
  for (let i = 0; i < n; i++) {
    const k = i % 10;
    if (k === 0) b.push({ type: 'header', data: { text: tag + ' Abschnitt ' + i, level: 2 } });
    else if (k === 5) b.push({ type: 'list', data: { style: 'unordered', meta: {}, items: [1, 2, 3].map((j) => ({ content: 'Punkt ' + i + '.' + j, meta: {}, items: [] })) } });
    else b.push({ type: 'paragraph', data: { text: tag + ' Absatz ' + i + ' mit <b>etwas</b> Text.' } });
  }
  return b;
}

(async () => {
  const br = await PW.chromium.launch({ channel: process.env.PW_CHANNEL || undefined });
  try {
    const ctx = await br.newContext({ viewport: { width: 1366, height: 860 }, locale: 'de-DE' });
    const p = await ctx.newPage(); const errs = [];
    p.on('pageerror', (e) => errs.push(e.message));
    await p.goto(BASE + '/login');
    await p.fill('input[name=username], input#username, input[type=text]', USER); await p.fill('input[type=password]', PASS);
    await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
    await sleep(800);
    const api = (m, u, b) => p.evaluate(async ([m, u, b]) => {
      const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
      return r.json().catch(() => ({}));
    }, [m, u, b]);
    const mk = async (title, blocks) => { const id = (await api('POST', '/api/notes', { title })).id; await api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks }), format: 'blocks' }); return id; };
    const blocksOf = async (id) => JSON.parse((await api('GET', '/api/notes/' + id)).body || '{}').blocks || [];

    const BIG = await mk('Leck gross', many(300, 'G'));
    const SMALL = await mk('Leck klein', [{ type: 'paragraph', data: { text: 'klein' } }]);
    const NEST = await mk('Leck verschachtelt', [{ type: 'paragraph', data: { text: 'oben' } },
      { type: 'toggle', data: { title: 'Klapp', open: true, blocks: many(20, 'T') } },
      { type: 'columns', data: { cols: [{ blocks: many(10, 'L') }, { blocks: many(10, 'R') }] } },
      { type: 'paragraph', data: { text: 'unten' } }]);
    const titles = { [BIG]: 'Leck gross', [SMALL]: 'Leck klein', [NEST]: 'Leck verschachtelt' };

    const cdp = await ctx.newCDPSession(p);
    await cdp.send('Performance.enable'); await cdp.send('HeapProfiler.enable');
    async function metrics() {
      await cdp.send('HeapProfiler.collectGarbage'); await sleep(150); await cdp.send('HeapProfiler.collectGarbage'); await sleep(150);
      const m = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map((x) => [x.name, x.value]));
      return { kb: Math.round(m.JSHeapUsedSize / 1024), nodes: m.Nodes, lst: m.JSEventListeners };
    }
    async function go(id, min) {
      await p.evaluate((id) => { location.hash = '#' + id; }, id);
      const t0 = Date.now();
      while (Date.now() - t0 < 15000) {
        if (await p.evaluate(([id, min, t]) => location.hash === '#' + id && document.getElementById('pg-title').value === t &&
          document.querySelectorAll('#pg-editor .ce-block').length >= min, [id, min, titles[id]])) break;
        await sleep(25);
      }
      await sleep(250);
    }
    await p.goto(BASE + '/notes#' + SMALL); await p.waitForSelector('#pg-editor .ce-paragraph'); await sleep(1000);

    // 1) Speicher: Wechsel grosse/verschachtelte Seite <-> kleine Seite
    for (const [name, X, min] of [['300 Bloecke', BIG, 300], ['Ausklappbar + Spalten', NEST, 40]]) {
      await go(X, min); await go(SMALL, 1);                          // einmal warm (Vendor geladen, Caches)
      const a = await metrics();
      for (let i = 0; i < N; i++) { await go(X, min); await go(SMALL, 1); }
      const b = await metrics();
      const per = { nodes: Math.round((b.nodes - a.nodes) / N), lst: Math.round((b.lst - a.lst) / N), kb: Math.round((b.kb - a.kb) / N) };
      // vorher: 300 Bloecke +2 164 Knoten / +1 477 Listener / +276 KB je Wechsel, verschachtelt +382 / +236 / +220 KB
      ok(`P01 ${name}: kein Zuwachs je Wechsel (${N}x, ${JSON.stringify(per)}, Knoten ${a.nodes}->${b.nodes})`, per.nodes < 30 && per.lst < 20 && per.kb < 80, { a, b });
    }

    // 2) Editor arbeitet nach vielen Wechseln normal: Enter teilt den Block (Block-Listener neu gebunden)
    await go(BIG, 300); await sleep(600);
    const last = p.locator('#pg-editor > .codex-editor > .codex-editor__redactor > .ce-block .ce-paragraph').last();
    await last.click(); await p.keyboard.press('End'); await p.keyboard.press('Enter'); await p.keyboard.type('Neu nach Wechseln', { delay: 15 });
    await sleep(2200);
    let bl = await blocksOf(BIG);
    ok('Enter nach vielen Wechseln legt einen neuen Block an und speichert', bl.length === 301 && bl[300].data.text === 'Neu nach Wechseln', bl.slice(-2));
    // Markdown-Kuerzel ersetzt den Block (Ersetzen-Pfad in Editor.js)
    await p.keyboard.press('Enter'); await p.keyboard.type('## Kuerzel', { delay: 20 }); await sleep(2200);
    bl = await blocksOf(BIG);
    ok('Markdown-Kuerzel "## " nach Wechseln -> Ueberschrift', bl.some((b) => b.type === 'header' && /Kuerzel/.test(b.data.text)), bl.slice(-2));
    // Backspace am Blockanfang fuehrt den Block zusammen (Block-Tastatur nach Ersetzen)
    await go(SMALL, 1); await go(BIG, 300); await sleep(600);
    const n0 = await p.evaluate(() => document.querySelectorAll('#pg-editor > .codex-editor > .codex-editor__redactor > .ce-block').length);
    const lastP = p.locator('#pg-editor > .codex-editor > .codex-editor__redactor > .ce-block .ce-paragraph', { hasText: 'Neu nach Wechseln' });
    await lastP.click(); await p.keyboard.press('End'); await p.keyboard.press('Enter'); await sleep(300); await p.keyboard.type('x'); await p.keyboard.press('Backspace'); await p.keyboard.press('Backspace');
    await sleep(400);
    const n1 = await p.evaluate(() => document.querySelectorAll('#pg-editor > .codex-editor > .codex-editor__redactor > .ce-block').length);
    ok(`Leerer Block per Backspace wieder entfernt (${n0} -> ${n1})`, n1 === n0);

    // 3) Ausklappbar nach Wechseln: Tippen im inneren Editor wird gespeichert
    await go(NEST, 40); await sleep(800);
    const inner = p.locator('.tgl-body .ce-paragraph', { hasText: 'T Absatz 1 ' }).first();
    await inner.click(); await p.keyboard.press('End'); await p.keyboard.type(' innen getippt', { delay: 15 });
    await sleep(2600);
    bl = await blocksOf(NEST);
    const tg = bl.find((b) => b.type === 'toggle');
    ok('Ausklappbar nach Wechseln: innerer Editor speichert', tg && JSON.stringify(tg.data.blocks).includes('innen getippt'), tg && tg.data.blocks.slice(0, 2));
    const col = bl.find((b) => b.type === 'columns');
    ok('Spalten bleiben vollstaendig erhalten', col && col.data.cols.length === 2 && col.data.cols[0].blocks.length === 10 && col.data.cols[1].blocks.length === 10);

    // 4) schnelles Wechseln ohne Warten: am Ende steht die zuletzt gewaehlte Seite vollstaendig da und speichert
    for (const id of [BIG, SMALL, NEST, BIG, SMALL, NEST]) await p.evaluate((id) => { location.hash = '#' + id; }, id);
    await go(NEST, 40); await sleep(1500);
    const shown = await p.evaluate(() => ({ t: document.getElementById('pg-title').value, tg: document.querySelectorAll('#pg-editor .tgl').length,
      top: document.querySelectorAll('#pg-editor > .codex-editor > .codex-editor__redactor > .ce-block').length }));
    ok('Schnelles Wechseln: letzte Seite steht vollstaendig da', shown.t === 'Leck verschachtelt' && shown.tg === 1 && shown.top === 4, shown);
    const unten = p.locator('#pg-editor > .codex-editor > .codex-editor__redactor > .ce-block .ce-paragraph', { hasText: 'unten' });
    await unten.click(); await p.keyboard.press('End'); await p.keyboard.type(' schnell', { delay: 15 }); await sleep(2400);
    bl = await blocksOf(NEST);
    ok('Schnelles Wechseln: Tippen danach wird gespeichert', bl.length === 4 && bl[3].data.text === 'unten schnell', bl.map((b) => b.type));
    const big = await blocksOf(BIG), small = await blocksOf(SMALL);
    ok('Schnelles Wechseln: andere Seiten unveraendert', big.length === 302 && small.length === 1 && small[0].data.text === 'klein', [big.length, small]);

    ok('keine Seitenfehler', !errs.length, errs.slice(0, 5));
  } finally { await br.close(); }
  const bad = res.filter((x) => !x).length;
  console.log(bad ? `${bad} von ${res.length} Pruefungen FEHLGESCHLAGEN` : `alle ${res.length} Pruefungen OK`);
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
