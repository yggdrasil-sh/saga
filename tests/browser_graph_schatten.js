// 2.6 (Bugs-DB 2601/2603, docs/MEMORY.md 10.4): Graph mit Schattenmodus - Verknuepfungen als eigene Kanten (Pixel an der
// Kantenmitte in --gr-mlink, dicker bei hoeherem Gewicht, "gemeinsam" gestrichelt), bewusst/unterbewusst unterscheidbar
// (Fuellung, Ring um den Ich-Kern), Schnappschuesse kleiner und gedimmt, Schatten-Treffer der letzten 24 h als
// gestrichelter Ring in --gr-shadow, Live-Schattenring nach recall (Ereignis memory.recalled mit schatten) und ruhiger Ring
// bei prefers-reduced-motion (keine Animationsschleife), Schalter "Verknuepfungen (Schattenmodus)"/"Schatten-Treffer"
// blenden aus und bleiben gespeichert ("Standard" setzt zurueck), Legende/Karte, hell/dunkel (Kontrast >= 3:1, nicht
// violett), Konto ohne Schattenmodus ohne Schalter und Kanten. Dazu Einstellungen > Gedaechtnis mit Rig-Daten
// (Spalte Sensibel, Nickerchen-Stand, Schattenmodus-Stand). Ohne Seitenfehler.
// Testdaten: tests/graph_schatten_rig.py (Konto SAGA_USER, erfundene Daten).
//   SAGA_BASE=... SAGA_USER=... SAGA_PASS=... [SHOTS=/ordner] [VORHER=1] node tests/browser_graph_schatten.js
// VORHER=1: nur Bilder (Graph hell/dunkel, Einstellungen hell/dunkel) - gegen den Stand ohne die Web-Aenderungen laufen lassen.
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const { execFileSync } = require('child_process');
const E = process.env, BASE = E.SAGA_BASE || E.BASE_URL, PRE = E.VORHER ? 'vorher' : 'nachher';
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c || i === undefined ? '' : ' -> ' + JSON.stringify(i).slice(0, 700))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); let v; while (Date.now() - t0 < (ms || 5000)) { v = await fn(); if (v) return v; await sleep(80); } return v; }
function rig(...a) {
  const out = execFileSync('python', ['tests/graph_schatten_rig.py', a[0], E.SAGA_USER].concat(a.slice(1)),
    { encoding: 'utf8', env: Object.assign({}, process.env, { PYTHONPATH: 'tests/pgshim:.' }) });
  const last = out.trim().split('\n').pop();
  try { return JSON.parse(last); } catch (e) { return last; }
}
async function session(br, opts) {
  const ctx = await br.newContext(Object.assign({ viewport: { width: 1280, height: 860 }, locale: 'de-DE', colorScheme: 'light' }, opts || {}));
  // Live-Kanal mitschreiben (live.js meldet 'live.hello', sobald /api/events verbunden ist)
  await ctx.addInitScript(() => { window.__live = []; document.addEventListener('hk-live', (e) => { window.__live.push(e.detail ? { type: e.detail.type, data: e.detail.data } : {}); }); });
  const p = await ctx.newPage(); p.errs = [];
  p.on('pageerror', (e) => p.errs.push(e.message));
  await p.goto(BASE + '/login');
  await p.fill('input[name=username]', E.SAGA_USER); await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  return p;
}
async function shot(p, n, o) {
  if (!E.SHOTS) return;
  if (o && o.el) { const h = await p.$(o.el); if (h) { await h.screenshot({ path: E.SHOTS + '/' + PRE + '-' + n + '.png' }); return; } }
  await p.screenshot({ path: E.SHOTS + '/' + PRE + '-' + n + '.png' });
}
const liveUp = (p) => until(() => p.evaluate(() => (window.__live || []).some((x) => x.type === 'live.hello')), 10000);
const settled = (p) => until(() => p.evaluate(() => { const g = HKGraph.current(); return g && g.view.alpha < 0.05 && !g.view.cam; }), 12000);
async function openGraph(p) {
  await p.goto(BASE + '/notes?t=' + Date.now()); await p.waitForSelector('#pg-graph-btn'); await sleep(800);
  await p.click('#pg-graph-btn'); await p.waitForSelector('.gr-full'); await sleep(600);
  await until(() => p.evaluate(() => { const g = HKGraph.current(); return g && g.view.nodes.length > 5 && g.view.alpha < 0.05; }), 12000);
  await liveUp(p);
  await p.mouse.move(2, 2);
  await p.evaluate(() => { const v = HKGraph.current().view; v.user(); v.fit(true); }); await sleep(400);
}
// Hilfen im Seitenkontext: Pixel (nicht vormultipliziert), Kamera auf eine Kante/einen Knoten
async function helpers(p) {
  await p.evaluate(() => {
    const V = () => HKGraph.current().view;
    const px = (v, x, y) => v.ctx.getImageData(Math.round(x * v.dpr), Math.round(y * v.dpr), 1, 1).data;
    const scr = (v, n) => [v.tx + n.x * v.k, v.ty + n.y * v.k];
    window.__amb = (d) => d[3] > 8 && d[0] - d[2] > 50;                                            // bernstein (--gr-mlink/--gr-mem)
    window.__teal = (d) => d[3] > 8 && d[1] - d[0] > 60 && d[2] - d[0] > 60 && Math.abs(d[1] - d[2]) < 40;   // tuerkis (--gr-shadow)
    window.__camEdge = (a, b, K) => {
      const v = V(), A = v.byId(a), B = v.byId(b); if (!A || !B) return false;
      v.user(); v.camTo({ k: K, tx: v.w / 2 - (A.x + B.x) / 2 * K, ty: v.h / 2 - (A.y + B.y) / 2 * K }, true); return true;
    };
    window.__camNode = (id, K) => { const v = V(), n = v.byId(id); if (!n) return false; v.center(n, K, true); return true; };
    // Kante a-b: je Punkt zwischen 30 und 70 % (ausserhalb von Knoten und deren Hof) quer zur Kante die Deckkraft der
    // bernsteinfarbenen Pixel aufsummieren; zurueck: groesste Summe und Anteil der Luecken entlang der Mittellinie
    window.__edge = (a, b) => {
      const v = V(), A = v.byId(a), B = v.byId(b); if (!A || !B) return null;
      const [ax, ay] = scr(v, A), [bx, by] = scr(v, B), L = Math.hypot(bx - ax, by - ay); if (L < 30) return { short: L };
      const ux = (bx - ax) / L, uy = (by - ay) / L, nx = -uy, ny = ux;
      const ns = v.nodes.map((n) => { const s = scr(v, n); return [s[0], s[1], v.rr(n) * v.k * 1.8 + 16]; });
      const free = (x, y) => !ns.some((q) => Math.hypot(q[0] - x, q[1] - y) < q[2]);
      let best = 0, pts = 0, cl = 0, gaps = 0;
      for (let s = Math.round(L * 0.3); s <= L * 0.7; s += 1) {     // 1-px-Schritte (Strichmuster 2 + 3 px)
        const cx = ax + ux * s, cy = ay + uy * s;
        if (!free(cx, cy)) continue;
        cl++; const c = px(v, cx, cy); if (!(window.__amb(c) && c[3] > 25)) gaps++;
        if (s % 4) continue;
        pts++;
        let sum = 0;
        for (let t = -8; t <= 8; t += 1) { const d = px(v, cx + nx * t, cy + ny * t); if (window.__amb(d)) sum += d[3]; }
        if (sum > best) best = sum;
      }
      return { sum: best, pts, gap: cl ? gaps / cl : 1, L };
    };
    // Ring um einen Knoten: Anteil der Pixel einer Farbe im Abstand rs + d (Bildschirm)
    window.__ring = (id, d, kind) => {
      const v = V(), n = v.byId(id); if (!n) return null;
      const [sx, sy] = scr(v, n), rs = v.rr(n) * v.k, f = kind === 'teal' ? window.__teal : window.__amb;
      let hit = 0, all = 0;
      for (let a = 0; a < 6.283; a += 0.04) for (const dd of [d - 0.6, d, d + 0.6]) { all++; if (f(px(v, sx + Math.cos(a) * (rs + dd), sy + Math.sin(a) * (rs + dd)))) hit++; }
      return hit / all;
    };
    // Fuellung: kleinste Deckkraft im Kreis bei 0,5 r (Kanten darunter erhoehen sie nur)
    window.__fill = (id) => {
      const v = V(), n = v.byId(id); if (!n) return null;
      const [sx, sy] = scr(v, n), rs = v.rr(n) * v.k; let m = 255;
      for (let a = 0; a < 6.283; a += 0.26) { const d = px(v, sx + Math.cos(a) * rs * 0.5, sy + Math.sin(a) * rs * 0.5); if (d[3] < m) m = d[3]; }
      return m;
    };
  });
}
const vs = (p) => p.evaluate(() => { const v = HKGraph.current().view; return { raf: v.raf, npulse: v.npulse, alpha: v.alpha, k: v.k }; });
const node = (p, id) => p.evaluate((id) => { const v = HKGraph.current().view, n = v.byId(id); return n && { id: n.id, sh: n.sh || 0, sz: n.sz || 0, sr: n.sr || 0, aw: !!n.aw,
  snap: !!n.snap, core: !!n.core, memn: !!n.memn, deg: n.deg, rad: v.rad(n), base: (2.5 + Math.sqrt(n.deg) * 1.6) * v.p.size, mf: n.mf || 0, hits: n.hits || 0 }; }, id);
async function recall(R, text) {
  const r = await fetch(BASE + '/api/v1/memory/recall', { method: 'POST', headers: { Authorization: 'Bearer ' + R.token, 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
  return { s: r.status, d: await r.json().catch(() => ({})) };
}
async function panelOpen(p) {
  if (await p.$eval('.gr-panel', (x) => x.hidden)) await p.click('.gr-panel-btn');
  await p.evaluate(() => { [].forEach.call(document.querySelectorAll('.gr-sec'), (d) => { d.open = true; }); });
}
const sw = (p, t) => p.locator('.gr-check', { hasText: t });
const prefs = (p) => p.evaluate(() => JSON.parse(localStorage.getItem('saga-graph') || HK.store('saga-graph') || '{}'));
async function settings(br, dark) {
  const s = await session(br, { viewport: { width: 1360, height: 2600 }, colorScheme: dark ? 'dark' : 'light' });
  await s.evaluate(() => fetch('/api/account/lang', { method: 'PUT', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: '{"lang":"de"}' }));
  await s.goto(BASE + '/dashboard?settings=memory'); await s.waitForSelector('.hk-dialog-set', { timeout: 8000 });
  await until(() => s.evaluate(() => !!document.querySelector('#hk-pane-memory .hk-mem-cols')), 8000); await sleep(700);
  return s;
}

let R = null;
(async () => {
  const br = await PW.chromium.launch();
  try {
    R = rig('setup');
    ok('Rig: Test-Gedaechtnis mit Schattenmodus angelegt', R && R.db && R.token && R.shadow === true && R.links.length === 9, R);
    const ROW = R.rows;
    // --- Einstellungen > Gedaechtnis mit Rig-Daten (Bilder; Pruefungen nur nachher) -----------------------------------
    for (const dark of [false, true]) {
      const s = await settings(br, dark);
      if (!E.VORHER && !dark) {
        const st = await s.evaluate(() => ({
          sens: (document.querySelector('.hk-mem-col[data-f=sensibel]') || {}).textContent || '',
          nap: (document.getElementById('hk-mem-nap') || {}).textContent || '',
          sw: (document.getElementById('hk-mem-shadow') || {}).checked, sst: (document.getElementById('hk-mem-shadow-state') || {}).textContent || '',
          forget: !!document.getElementById('hk-mem-forget') && !document.getElementById('hk-mem-forget').closest('.hk-actions').hidden }));
        ok('Einstellungen: Spalte „Sensibel“ gefunden', /Sensibel/.test(st.sens) && /gefunden/.test(st.sens), st);
        ok('Einstellungen: Nickerchen „3 Schnappschüsse · zuletzt …“', /^3 Schnappschüsse · zuletzt /.test(st.nap), st);
        ok('Einstellungen: Schattenmodus an, „9 Verknüpfungen · 5 Spuren (30 Tage)“, „Spuren löschen“ sichtbar', st.sw === true && st.sst === '9 Verknüpfungen · 5 Spuren (30 Tage)' && st.forget, st);
      }
      await shot(s, 'einstellungen-gedaechtnis-' + (dark ? 'dunkel' : 'hell'), { el: '#hk-pane-memory' });
      ok('Einstellungen: keine Seitenfehler (' + (dark ? 'dunkel' : 'hell') + ')', !s.errs.length, s.errs);
      await s.context().close();
    }
    const p = await session(br);
    await openGraph(p); await helpers(p);
    if (E.VORHER) {
      await shot(p, 'graph-hell');
      await p.evaluate((id) => window.__camNode(id, 2.2), ROW.Tomaten); await sleep(200); await shot(p, 'graph-hell-nah');
      const d = await session(br, { colorScheme: 'dark' });
      await openGraph(d); await helpers(d); await shot(d, 'graph-dunkel');
      await d.evaluate((id) => window.__camNode(id, 2.2), ROW.Tomaten); await sleep(200); await shot(d, 'graph-dunkel-nah');
      ok('Vorher-Bilder', true);
      return;
    }
    // --- Daten ----------------------------------------------------------------------------------------------------------
    const data = await p.evaluate(() => fetch('/api/graph', { headers: { 'X-Saga': '1' } }).then((r) => r.json()));
    const m = data.mem || {};
    ok('/api/graph: mem.shadow, 9 Links, hits24 (Hochbeet 3x), core, 3 Schnappschuesse', m.shadow === true && (m.links || []).length === 9 && m.core === R.core
       && (m.snap || []).length === 3 && (m.hits24 || []).some((h) => h[0] === ROW.Hochbeet && h[1] === 3), m);
    const W = {}; (m.links || []).forEach((l) => { W[l[0] + '-' + l[1]] = l[2]; });
    const key = (a, b) => Math.min(a, b) + '-' + Math.max(a, b);
    console.log('INFO Gewichte (w_eff): ' + JSON.stringify(W));
    const me = await p.evaluate(() => HKGraph.current().view.edges.filter((e) => e.k === 'm').length);
    ok('Graph: 9 Kanten der Art m (Verknuepfungen)', me === 9, me);
    // --- Kanten sichtbar, dicker bei hoeherem Gewicht, gemeinsam gestrichelt --------------------------------------------
    const K = 2.4;
    async function edgeAt(a, b) { await p.evaluate(([a, b, K]) => window.__camEdge(a, b, K), [a, b, K]); await sleep(60); return p.evaluate(([a, b]) => window.__edge(a, b), [a, b]); }
    const strong = await edgeAt(ROW.Tomaten, ROW.Basilikum), weak = await edgeAt(ROW.Wien, ROW.Packliste);
    const solid = await edgeAt(ROW.Tomaten, ROW.Kompost), dashed = await edgeAt(ROW.Nachtzug, ROW.Wien);
    ok('Verknuepfung sichtbar: bernsteinfarbene Pixel an der Kantenmitte (Tomaten–Basilikum, Gewicht 1,0)', strong && strong.pts > 3 && strong.sum > 300, strong);
    ok('Schwache Verknuepfung (Wien–Packliste, abgeklungen) trotzdem sichtbar', weak && weak.pts > 3 && weak.sum > 20, weak);
    ok('Dicker/kraeftiger bei hoeherem Gewicht: Tomaten–Basilikum > 3x Wien–Packliste', strong && weak && strong.sum > 3 * weak.sum, [strong, weak, W[key(ROW.Wien, ROW.Packliste)]]);
    ok('Herkunft „gemeinsam“ gestrichelt (Nachtzug–Wien), „ähnlich“ durchgezogen (Tomaten–Kompost)', dashed && solid && dashed.gap > 0.25 && solid.gap < 0.08, [dashed, solid]);
    await shot(p, 'graph-kante-nah');
    await p.evaluate(() => { const v = HKGraph.current().view; v.user(); v.fit(true); }); await sleep(300);
    // --- bewusst / unterbewusst / Schnappschuss ------------------------------------------------------------------------
    await p.evaluate((id) => window.__camNode(id, 1.6), ROW.Tomaten); await sleep(100);
    const nT = await node(p, ROW.Tomaten), nH = await node(p, ROW.Hochbeet), nC = await node(p, R.core), nS = await node(p, R.snaps[0]);
    ok('bewusst: Tomaten (vor 5 min) und Ich-Kern; unterbewusst: Hochbeet (nie abgerufen)', nT.aw && nC.aw && !nH.aw && nT.memn && nH.memn && nC.core, [nT, nH, nC]);
    const fillOf = async (id) => { await p.evaluate((id) => window.__camNode(id, 2), id); await sleep(60); return p.evaluate((id) => window.__fill(id), id); };
    const fT = await fillOf(ROW.Tomaten), fK = await fillOf(ROW.Kaffee), fH = await fillOf(ROW.Hochbeet), fS = await fillOf(ROW.Sauerteig), fN = await fillOf(R.snaps[0]);
    ok('Fuellung: bewusst voll (>= 240), unterbewusst blasser (110–175)', fT >= 240 && fK >= 240 && fH >= 110 && fH <= 175 && fS >= 110 && fS <= 175, [fT, fK, fH, fS]);
    ok('Schnappschuss: Deckkraft ~0,4 (< 125) und Radius x0,7', fN < 125 && Math.abs(nS.rad / nS.base - 0.7) < 0.02 && nS.snap, [fN, nS]);
    ok('Ich-Kern: Radius x1,15', Math.abs(nC.rad / nC.base - 1.15) < 0.02, nC);
    await p.evaluate((id) => window.__camNode(id, 2), R.core); await sleep(60);
    const rC = await p.evaluate((id) => window.__ring(id, 1.2, 'amb'), R.core);
    await p.evaluate((id) => window.__camNode(id, 2), R.db); await sleep(60);
    const rD = await p.evaluate((id) => window.__ring(id, 1.2, 'amb'), R.db);
    ok('bewusst: Ring in --gr-mem um den Ich-Kern, keiner um die Datenbank', rC > 0.3 && rD < 0.05, [rC, rD]);
    // --- Schatten-Treffer 24 h --------------------------------------------------------------------------------------------
    const ringOf = async (id) => { await p.evaluate((id) => window.__camNode(id, 2), id); await sleep(60); return p.evaluate((id) => window.__ring(id, 3, 'teal'), id); };
    const tH = await ringOf(ROW.Hochbeet), tK = await ringOf(ROW.Kompost), tB0 = await ringOf(ROW.Basilikum);
    ok('Schatten-Treffer: gestrichelter Ring in --gr-shadow um Hochbeet, keiner um Kompost/Basilikum', tH > 0.2 && tH < 0.85 && tK < 0.03 && tB0 < 0.03, [tH, tK, tB0]);
    await p.evaluate((id) => window.__camNode(id, 2.2), ROW.Tomaten); await sleep(100);
    await shot(p, 'graph-hell-nah');
    await p.evaluate(() => { const v = HKGraph.current().view; v.user(); v.fit(true); }); await sleep(300);
    await shot(p, 'graph-hell');
    const lg = await p.textContent('.gr-legend');
    ok('Legende: „Gerade bewusst“, „Verknüpfung“, „Wäre aufgetaucht“', /Gerade bewusst/.test(lg) && /Verknüpfung/.test(lg) && /Wäre aufgetaucht/.test(lg), lg);
    await p.evaluate((id) => { const v = HKGraph.current().view; v.setSel(v.byId(id), 'key'); }, ROW.Hochbeet); await sleep(200);
    const cs = await p.textContent('.gr-card-s');
    ok('Karte: „3× wäre aufgetaucht (24 h) · 2 Verknüpfungen“', /3× wäre aufgetaucht \(24 h\)/.test(cs) && /2 Verknüpfungen/.test(cs), cs);
    await p.keyboard.press('Escape'); await sleep(150);
    const stat = await p.textContent('.gr-stat');
    ok('Kopfzeile zählt Verknüpfungen eigens („· 9 Verknüpfungen“)', /9 Verknüpfungen/.test(stat), stat);
    // --- Live: recall -> Schattenring --------------------------------------------------------------------------------------
    const b0 = await node(p, ROW.Basilikum);
    const rc = await recall(R, R.query);
    ok('REST recall: 200, ms.schatten, Basilikum kein Basis-Treffer', rc.s === 200 && rc.d.ms && typeof rc.d.ms.schatten === 'number' && !rc.d.treffer.some((x) => x.id === ROW.Basilikum), rc);
    const ev = await until(() => p.evaluate(() => (window.__live || []).filter((x) => x.type === 'memory.recalled').pop()), 5000);
    ok('Ereignis memory.recalled mit schatten (Basilikum)', ev && ev.data && (ev.data.schatten || []).indexOf(R.schatten_id) >= 0, ev);
    const lv = await until(async () => { const z = await node(p, ROW.Basilikum); return z.sz ? z : null; }, 4000);
    const st1 = await vs(p);
    ok('Live: Schattenring dehnt sich aus (Animation laeuft), Zaehler +1 ohne Neuladen', lv && lv.sz > 0 && lv.sh === b0.sh + 1 && st1.npulse > 0 && st1.raf, [b0, lv, st1]);
    await p.evaluate((id) => window.__camNode(id, 2.2), ROW.Basilikum); await sleep(250);
    await shot(p, 'graph-schatten-live');
    await sleep(2600);
    const st2 = await vs(p), b1 = await node(p, ROW.Basilikum);
    ok('Live-Ring endet (~1,2 s), danach ruhiger Ring, keine Dauerschleife', !b1.sz && !st2.npulse && !st2.raf && (await p.evaluate((id) => window.__ring(id, 3, 'teal'), ROW.Basilikum)) > 0.15, [b1, st2]);
    // --- Schalter ------------------------------------------------------------------------------------------------------------
    await p.evaluate(() => { const v = HKGraph.current().view; v.user(); v.fit(true); }); await sleep(200);
    await panelOpen(p);
    const sL = sw(p, 'Verknüpfungen (Schattenmodus)'), sS = sw(p, 'Schatten-Treffer');
    ok('Schalter „Verknüpfungen (Schattenmodus)“ und „Schatten-Treffer“ sichtbar und an', await sL.isVisible() && await sS.isVisible() && await sL.locator('input').isChecked() && await sS.locator('input').isChecked());
    await sL.click(); await sleep(300); await settled(p);
    const offL = await p.evaluate(() => HKGraph.current().view.edges.filter((e) => e.k === 'm').length);
    await p.click('.gr-panel-btn'); await sleep(200);
    const so = await edgeAt(ROW.Tomaten, ROW.Basilikum), lgo = await p.textContent('.gr-legend');
    ok('Verknüpfungen aus: keine Kanten m, keine bernsteinfarbenen Pixel an der Kantenmitte, Legende ohne „Verknüpfung“', offL === 0 && so && so.sum < 60 && !/Verknüpfung/.test(lgo), [offL, so, lgo]);
    await panelOpen(p);
    await sS.click(); await sleep(300); await settled(p);
    await p.click('.gr-panel-btn'); await sleep(200);
    const tHo = await ringOf(ROW.Hochbeet), hS = await node(p, ROW.Hochbeet);
    ok('Schatten-Treffer aus: kein Ring, Legende ohne „Wäre aufgetaucht“', tHo < 0.03 && !hS.sh && !/Wäre aufgetaucht/.test(await p.textContent('.gr-legend')), [tHo, hS]);
    await p.evaluate(() => { const v = HKGraph.current().view; v.user(); v.fit(true); }); await sleep(200);
    await shot(p, 'graph-schalter-aus');
    const pr = await prefs(p);
    ok('Schalter gespeichert (memlinks=false, memshadow=false)', pr.memlinks === false && pr.memshadow === false, pr);
    await p.keyboard.press('Escape'); await sleep(200);
    await openGraph(p); await helpers(p);
    ok('nach erneutem Oeffnen weiter aus', await p.evaluate(() => !HKGraph.current().view.edges.some((e) => e.k === 'm')) && !(await node(p, ROW.Hochbeet)).sh);
    await panelOpen(p);
    await p.click('.gr-panel-foot .btn:nth-child(2)'); await sleep(400); await settled(p);           // "Standard"
    const pr2 = await prefs(p), me2 = await p.evaluate(() => HKGraph.current().view.edges.filter((e) => e.k === 'm').length);
    ok('„Standard“ schaltet beide wieder an', pr2.memlinks === true && pr2.memshadow === true && me2 === 9 && (await node(p, ROW.Hochbeet)).sh === 3, [pr2, me2]);
    await p.click('.gr-panel-btn');
    ok('keine Seitenfehler (hell)', !p.errs.length, p.errs);
    // --- weniger Bewegung: ruhiger Ring, keine Animationsschleife ----------------------------------------------------------
    const r = await session(br, { reducedMotion: 'reduce' });
    await openGraph(r); await helpers(r);
    const rb0 = await node(r, ROW.Basilikum);
    await recall(R, R.query);
    const rp = await until(async () => { const z = await node(r, ROW.Basilikum); return z.sr ? z : null; }, 5000);
    const raf = []; for (let i = 0; i < 8; i++) { raf.push((await vs(r)).raf); await sleep(80); }
    ok('prefers-reduced-motion: ruhiger Schattenring, keine Ausdehnung, keine Animationsschleife', rp && rp.sr > 0 && !rp.sz && rp.sh === rb0.sh + 1 && raf.every((x) => !x), [rp, raf]);
    await r.evaluate((id) => window.__camNode(id, 2.2), ROW.Basilikum); await sleep(150);
    ok('prefers-reduced-motion: Ring sichtbar (gestrichelt, --gr-shadow)', (await r.evaluate((id) => window.__ring(id, 6, 'teal'), ROW.Basilikum)) > 0.15);
    await shot(r, 'graph-schatten-ruhig');
    await sleep(4300);
    ok('prefers-reduced-motion: ruhiger Ring nach 4 s weg', !(await node(r, ROW.Basilikum)).sr && !(await vs(r)).raf);
    ok('keine Seitenfehler (weniger Bewegung)', !r.errs.length, r.errs);
    await r.context().close();
    // --- Farben hell/dunkel ------------------------------------------------------------------------------------------------
    const pal = async (q) => q.evaluate(() => { const s = getComputedStyle(document.body); return { ml: s.getPropertyValue('--gr-mlink').trim(), sh: s.getPropertyValue('--gr-shadow').trim(), bg: s.getPropertyValue('--bg').trim(), db: s.getPropertyValue('--gr-db').trim() }; });
    function lum(h) { const c = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((x) => x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4)); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; }
    function cr(a, b) { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
    function hue(h) { const [r1, g1, b1] = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255), mx = Math.max(r1, g1, b1), mn = Math.min(r1, g1, b1), d = mx - mn;
      if (!d) return -1; let x = mx === r1 ? ((g1 - b1) / d) % 6 : mx === g1 ? (b1 - r1) / d + 2 : (r1 - g1) / d + 4; x *= 60; return x < 0 ? x + 360 : x; }
    const ph = await pal(p);
    const d = await session(br, { colorScheme: 'dark' });
    await openGraph(d); await helpers(d);
    const pd = await pal(d);
    const good = (q) => [q.ml, q.sh].every((c) => /^#[0-9a-f]{6}$/i.test(c) && cr(c, q.bg) >= 3 && !(hue(c) >= 250 && hue(c) <= 320)) && q.ml !== q.db && q.sh !== q.db;
    ok('Tokens --gr-mlink/--gr-shadow hell und dunkel verschieden, Kontrast >= 3:1 zu --bg, nicht violett', good(ph) && good(pd) && ph.ml !== pd.ml && ph.sh !== pd.sh,
       [ph, pd, cr(ph.ml, ph.bg), cr(ph.sh, ph.bg), cr(pd.ml, pd.bg), cr(pd.sh, pd.bg)]);
    await d.evaluate(([a, b]) => window.__camEdge(a, b, 2.4), [ROW.Tomaten, ROW.Basilikum]); await sleep(60);
    const de = await d.evaluate(([a, b]) => window.__edge(a, b), [ROW.Tomaten, ROW.Basilikum]);
    await d.evaluate((id) => window.__camNode(id, 2), ROW.Hochbeet); await sleep(60);
    const dt = await d.evaluate((id) => window.__ring(id, 3, 'teal'), ROW.Hochbeet);
    ok('dunkel: Verknüpfung und Schatten-Ring sichtbar', de && de.sum > 300 && dt > 0.2, [de, dt]);
    await d.evaluate(() => { const v = HKGraph.current().view; v.user(); v.fit(true); }); await sleep(300);
    await shot(d, 'graph-dunkel');
    await d.evaluate((id) => window.__camNode(id, 2.2), ROW.Tomaten); await sleep(200);
    await shot(d, 'graph-dunkel-nah');
    ok('keine Seitenfehler (dunkel)', !d.errs.length, d.errs);
    await d.context().close();
    await p.context().close();
    // --- Schattenmodus aus: keine Schalter, keine Kanten, keine Ringe ------------------------------------------------------
    const R2 = rig('setup', '--aus');
    ok('Rig: Schattenmodus aus', R2 && R2.shadow === false, R2);
    const q = await session(br);
    const d2 = await q.evaluate(() => fetch('/api/graph', { headers: { 'X-Saga': '1' } }).then((r) => r.json()));
    ok('/api/graph ohne Schattenmodus: shadow false, ohne links/hits24', d2.mem && d2.mem.shadow === false && !('links' in d2.mem) && !('hits24' in d2.mem), d2.mem);
    await openGraph(q); await helpers(q);
    await panelOpen(q);
    const vis = { mem: await sw(q, 'Gedächtnis hervorheben').isVisible(), ml: await sw(q, 'Verknüpfungen (Schattenmodus)').isVisible(), ms: await sw(q, 'Schatten-Treffer').isVisible() };
    ok('ohne Schattenmodus: Schalter Verknüpfungen/Schatten-Treffer ausgeblendet („Gedächtnis hervorheben“ bleibt)', vis.mem && !vis.ml && !vis.ms, vis);
    await q.click('.gr-panel-btn');
    const n2 = await q.evaluate(() => { const v = HKGraph.current().view; return { m: v.edges.filter((e) => e.k === 'm').length, sh: v.nodes.filter((n) => n.sh).length }; });
    const lg2 = await q.textContent('.gr-legend');
    ok('ohne Schattenmodus: keine Kanten m, keine Schatten-Ringe, Legende ohne beides; bewusst bleibt', !n2.m && !n2.sh && !/Verknüpfung|Wäre aufgetaucht/.test(lg2) && /Gerade bewusst/.test(lg2), [n2, lg2]);
    await shot(q, 'graph-aus-hell');
    ok('keine Seitenfehler (aus)', !q.errs.length, q.errs);
    await q.context().close();
  } catch (e) { ok('Ablauf', false, String(e && e.stack || e).slice(0, 600)); }
  finally {
    try { rig('clean'); } catch (e) { /* egal */ }
    await br.close();
  }
  const n = res.filter(Boolean).length;
  console.log(`\nBROWSER-GRAPH-SCHATTEN ${n}/${res.length} - ` + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
