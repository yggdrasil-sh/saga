// 2.5 Graph: Ueberlagerung "Alle Seiten" / "Lokal" - laedt, zeichnet, Ueberfahren hebt Knoten + Nachbarn weich hervor
// (auch knapp daneben), lokaler Graph nur auf Wunsch (Knopf bei "Verlinkt von", Seitenmenue "…"), Suche + Enter oeffnet,
// Filter wirken, Tastatur, Esc schliesst, Handy (Tippen -> Karte "Öffnen"), prefers-reduced-motion, keine Seitenfehler.
// Gegen einen laufenden Test-Stack:
//   SAGA_BASE=http://host:8099 SAGA_USER=konto SAGA_PASS=... [PLAYWRIGHT=playwright-core] [SHOTS=/ordner] node tests/browser_graph.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c || i === undefined ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function session(br, opts) {
  const ctx = await br.newContext(Object.assign({ viewport: { width: 1280, height: 860 }, locale: 'de-DE' }, opts || {}));
  const p = await ctx.newPage(); p.errs = [];
  p.on('pageerror', (e) => p.errs.push(e.message));
  await p.goto(BASE + '/login');
  await p.fill('input[name=username]', E.SAGA_USER); await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => { const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }); return r.json().catch(() => ({})); }, [m, u, b]);
  return p;
}
async function shot(p, n) { if (E.SHOTS) await p.screenshot({ path: E.SHOTS + '/' + n + '.png' }); }
// Bildschirmposition eines Knotens (nach Titel) in der offenen Ueberlagerung
const nodeXY = (p, t) => p.evaluate((t) => { const v = HKGraph.current().view, r = v.c.getBoundingClientRect(), n = v.nodes.find((n) => n.title === t);
  return n && { x: r.left + v.tx + n.x * v.k, y: r.top + v.ty + n.y * v.k }; }, t);
const vstate = (p) => p.evaluate(() => { const v = HKGraph.current().view; return { hover: v.hover && v.hover.title, sel: v.sel && v.sel.title, dim: v.dim, alpha: v.alpha }; });
async function until(fn, ms) { const t0 = Date.now(); let v; while (Date.now() - t0 < (ms || 5000)) { v = await fn(); if (v) return v; await sleep(100); } return v; }
(async () => {
  const br = await PW.chromium.launch();
  const made = [];
  try {
    const p = await session(br);
    const mk = async (title, extra) => { const id = (await p.api('POST', '/api/notes', Object.assign({ title }, extra || {}))).id; made.push(id); return id; };
    const link = (id) => ({ type: 'paragraph', data: { text: '<a href="/notes#' + id + '">x</a>' } });
    const hub = await mk('Graph Mitte');
    const ids = [];
    for (let i = 0; i < 12; i++) ids.push(await mk('Graph Blatt ' + i, i < 4 ? { parent_id: hub } : {}));
    await p.api('PATCH', '/api/notes/' + hub, { format: 'blocks', body: JSON.stringify({ blocks: ids.slice(4, 10).map(link) }), tags: ['grafik'] });
    await p.api('PATCH', '/api/notes/' + ids[10], { format: 'blocks', body: JSON.stringify({ blocks: [link(hub), link(ids[11])] }), tags: ['grafik'] });
    await sleep(2500);
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + hub); await p.waitForSelector('#pg-title'); await sleep(1500);
    await p.evaluate(() => { const s = document.getElementById('pg-backlinks'); if (s) s.scrollIntoView(); });
    await sleep(1200);
    // Lokaler Graph nicht mehr standardmaessig in der Seite - nur ein kleiner Knopf bei "Verlinkt von"
    const inl = await p.evaluate(() => ({ canvas: !!document.querySelector('#page canvas'), btn: !!document.querySelector('#pg-backlinks:not([hidden]) #pg-local-graph') }));
    ok('Seite: kein Graph in der Seite, Knopf "Lokaler Graph" bei "Verlinkt von"', !inl.canvas && inl.btn, inl);
    await shot(p, 'graph-seite');
    await p.click('#pg-local-graph'); await p.waitForSelector('.gr-full'); await sleep(2000);
    const loc = await p.evaluate(() => ({ label: document.querySelector('.gr-canvas').getAttribute('aria-label'), mode: document.querySelector('.gr-seg-b[aria-pressed="true"]').textContent, depth: !document.querySelector('.gr-depth').hidden }));
    ok('Lokaler Graph als Ansicht der Ueberlagerung (11 Nachbarn, Tiefe waehlbar)', /11/.test(loc.label || '') && loc.mode === 'Lokal' && loc.depth, loc);
    await shot(p, 'graph-lokal');
    await p.click('.gr-seg-b >> text=Alle Seiten'); await sleep(2500);
    const st = await p.textContent('.gr-stat');
    ok('Ganzer Graph geladen', /Seiten/.test(st) && !/nicht geladen/.test(st), st);
    const nonblank = await p.evaluate(() => { const c = document.querySelector('.gr-canvas'), x = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < x.length; i += 40) if (x[i] > 0) n++; return n; });
    ok('Canvas gezeichnet', nonblank > 50, nonblank);
    // Ueberfahren: trifft auch 7 px daneben, hebt weich hervor (Zwischenwert), Nachbarn beschriftet
    await until(async () => (await vstate(p)).alpha < 0.05, 8000);
    const xy = await nodeXY(p, 'Graph Mitte');
    await p.mouse.move(xy.x + 60, xy.y + 60); await sleep(250);
    await p.mouse.move(xy.x + 7, xy.y);
    const s1 = await vstate(p); await sleep(60); const s2 = await vstate(p); await sleep(400); const s3 = await vstate(p);
    ok('Ueberfahren trifft den Knoten (7 px daneben)', s1.hover === 'Graph Mitte', s1);
    ok('Hervorheben blendet weich ein (~180 ms, kein hartes Umschalten)', s2.dim > 0 && s2.dim < 1 && s3.dim === 1, [s2.dim, s3.dim]);
    await shot(p, 'graph-hover');
    await p.mouse.move(5, 400); await sleep(400);
    ok('Zeiger weg: Hervorhebung blendet aus', (await vstate(p)).dim === 0 && !(await vstate(p)).hover);
    // Ziehen: Simulation laeuft weiter (Nachbarn folgen)
    const xy2 = await nodeXY(p, 'Graph Mitte');
    await p.mouse.move(xy2.x, xy2.y); await p.mouse.down(); await p.mouse.move(xy2.x + 80, xy2.y + 40, { steps: 8 }); await sleep(150);
    const dragA = (await vstate(p)).alpha; await p.mouse.up(); await sleep(200);
    ok('Knoten ziehen: Simulation laeuft mit', dragA >= 0.25 && !!(await p.$('.gr-full')), dragA);
    // Tastatur: Pfeil waehlt eine Seite, Ansage fuer Screenreader
    await p.focus('.gr-canvas'); await p.keyboard.press('ArrowRight'); await sleep(300);
    const ks = await vstate(p);
    ok('Tastatur: Pfeiltaste waehlt eine Seite (Karte sichtbar)', !!ks.sel && !(await p.$eval('.gr-card', (c) => c.hidden)), ks);
    await p.keyboard.press('Escape'); await sleep(200);
    ok('Esc hebt erst die Auswahl auf', !!(await p.$('.gr-full')) && !(await vstate(p)).sel);
    await p.fill('.gr-search', 'Graph Blatt 11'); await sleep(400);
    await p.press('.gr-search', 'Enter'); await sleep(1500);
    ok('Suche + Enter oeffnet die Seite, Graph zu', !(await p.$('.gr-full')) && (await p.inputValue('#pg-title')) === 'Graph Blatt 11');
    // Seitenmenue "…" > "Lokaler Graph"
    await p.click('#pg-more'); await sleep(300);
    await p.click('.menu-item:has-text("Lokaler Graph")'); await p.waitForSelector('.gr-full'); await sleep(1500);
    const lm = await p.evaluate(() => document.querySelector('.gr-canvas').getAttribute('aria-label'));
    ok('Seitenmenue "…" oeffnet den lokalen Graph', /Graph Blatt 11/.test(lm) && /1 verbundene Seite\b/.test(lm), lm);
    await p.keyboard.press('Escape'); await sleep(300);
    await p.click('#pg-graph-btn'); await p.waitForSelector('.gr-full'); await sleep(1500);
    if (await p.$eval('.gr-panel', (x) => x.hidden)) await p.click('.gr-panel-btn');
    const n0 = await p.evaluate(() => document.querySelector('.gr-stat').textContent);
    const tg = p.locator('.gr-check', { hasText: 'Tags als Knoten' }); await tg.click(); await sleep(800);
    const n1 = await p.evaluate(() => document.querySelector('.gr-stat').textContent);
    ok('Filter "Tags als Knoten" aendert den Graph', n0 !== n1, [n0, n1]);
    await tg.click(); await p.click('.gr-panel-btn');
    await p.keyboard.press('Escape'); await sleep(300);
    ok('Esc schliesst', !(await p.$('.gr-full')));
    ok('keine Seitenfehler', !p.errs.length, p.errs);
    // weniger Bewegung: Hervorhebung sofort, ohne Uebergang
    const r = await session(br, { reducedMotion: 'reduce' });
    await r.goto(BASE + '/notes?t=' + Date.now() + '#' + hub); await r.waitForSelector('#pg-title'); await sleep(1000);
    await r.click('#pg-graph-btn'); await r.waitForSelector('.gr-full'); await sleep(2000);
    const rxy = await nodeXY(r, 'Graph Mitte'); await r.mouse.move(rxy.x, rxy.y); await sleep(60);
    const rs = await vstate(r);
    ok('prefers-reduced-motion: ruhender Graph, Hervorhebung ohne Uebergang', rs.hover === 'Graph Mitte' && rs.dim === 1 && rs.alpha === 0, rs);
    await r.context().close();
    // Handy + dunkel
    const m = await session(br, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme: 'dark' });
    await m.goto(BASE + '/notes?t=' + Date.now() + '#' + hub); await m.waitForSelector('#pg-title'); await sleep(1200);
    await m.evaluate(() => window.HKGraph.open(null)); await m.waitForSelector('.gr-full'); await sleep(2500);
    const ov = await m.evaluate(() => { const h = document.querySelector('.gr-head').getBoundingClientRect(), x = document.querySelector('.gr-close').getBoundingClientRect();
      return { sw: document.documentElement.scrollWidth, vw: innerWidth, panel: document.querySelector('.gr-panel').hidden, closeTop: x.top - h.top }; });
    ok('Handy: kein Ueberlauf, Einstellungen eingeklappt, Schliessen in der ersten Zeile', ov.sw <= ov.vw && ov.panel && ov.closeTop < 20, ov);
    await until(async () => (await vstate(m)).alpha < 0.05, 8000);
    const mxy = await nodeXY(m, 'Graph Mitte'); await m.touchscreen.tap(mxy.x, mxy.y); await sleep(500);
    const card = await m.evaluate(() => ({ hidden: document.querySelector('.gr-card').hidden, t: document.querySelector('.gr-card-t').textContent, open: !!document.querySelector('.gr-full') }));
    ok('Handy: Tippen hebt hervor und zeigt "Öffnen" (springt nicht sofort weg)', !card.hidden && /Graph Mitte/.test(card.t) && card.open, card);
    await shot(m, 'graph-handy-dunkel');
    await m.click('.gr-card-o'); await sleep(1500);
    ok('Handy: "Öffnen" oeffnet die Seite', !(await m.$('.gr-full')) && (await m.inputValue('#pg-title')) === 'Graph Mitte');
    ok('Handy: keine Seitenfehler', !m.errs.length, m.errs);
  } catch (e) { ok('Ablauf', false, String(e && e.stack || e).slice(0, 500)); }
  finally {
    try { const p2 = br.contexts()[0].pages()[0]; for (const id of made) await p2.evaluate(async (id) => { await fetch('/api/notes/' + id, { method: 'DELETE', headers: { 'X-Saga': '1' } }); }, id); } catch (e) { /* egal */ }
    await br.close();
  }
  const n = res.filter(Boolean).length;
  console.log(`\nBROWSER-GRAPH ${n}/${res.length} - ` + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
