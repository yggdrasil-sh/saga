// 2.6 (Bugs-DB 2595, docs/MEMORY.md 10): Graph mit Gedaechtnis - oft abgerufene Erinnerungen groesser (bis x1,8),
// kuerzlich abgerufene leuchten (Pixel im Hof um den Knoten), Legende "Kuerzlich abgerufen", Karte mit Zahl der Abrufe,
// recall ueber REST laesst die Treffer in der offenen Ansicht pulsieren (Live-Ereignis memory.recalled, Zaehler ohne
// Neuladen), Schalter "Gedaechtnis hervorheben" (aus = wie vorher, gespeichert), prefers-reduced-motion: ruhiger Ring statt
// Puls (keine Animationsschleife), hell/dunkel, Konto ohne Gedaechtnis: kein Schalter. Ohne Seitenfehler.
// Testdaten: tests/graph_gedaechtnis_rig.py (Konto SAGA_USER), zweites Konto SAGA_USER2 ohne Gedaechtnis.
//   SAGA_BASE=... SAGA_USER=... SAGA_PASS=... SAGA_USER2=... SAGA_PASS2=... [SHOTS=/ordner] [VORHER=1] node tests/browser_graph_gedaechtnis.js
// VORHER=1: nur Bilder (hell/dunkel, Ausschnitt) vom Stand ohne Hervorhebung (gegen den alten Code laufen lassen).
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const { execFileSync } = require('child_process');
const E = process.env, BASE = E.SAGA_BASE || E.BASE_URL, PRE = E.VORHER ? 'vorher' : 'nachher';
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c || i === undefined ? '' : ' -> ' + JSON.stringify(i).slice(0, 700))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); let v; while (Date.now() - t0 < (ms || 5000)) { v = await fn(); if (v) return v; await sleep(80); } return v; }
function rig(a) {
  const out = execFileSync('python', ['tests/graph_gedaechtnis_rig.py', a, E.SAGA_USER], { encoding: 'utf8', env: Object.assign({}, process.env, { PYTHONPATH: 'tests/pgshim:.' }) });
  const last = out.trim().split('\n').pop();
  try { return JSON.parse(last); } catch (e) { return last; }
}
async function session(br, user, pass, opts) {
  const ctx = await br.newContext(Object.assign({ viewport: { width: 1280, height: 860 }, locale: 'de-DE' }, opts || {}));
  const p = await ctx.newPage(); p.errs = [];
  p.on('pageerror', (e) => p.errs.push(e.message));
  await p.goto(BASE + '/login');
  await p.fill('input[name=username]', user); await p.fill('input[type=password]', pass);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => { const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }); return r.json().catch(() => ({})); }, [m, u, b]);
  return p;
}
async function shot(p, n, clip) { if (E.SHOTS) await p.screenshot({ path: E.SHOTS + '/' + PRE + '-' + n + '.png', clip: clip }); }
async function openGraph(p) {
  await p.goto(BASE + '/notes?t=' + Date.now()); await p.waitForSelector('#pg-graph-btn'); await sleep(800);
  await p.click('#pg-graph-btn'); await p.waitForSelector('.gr-full'); await sleep(600);
  await until(() => p.evaluate(() => { const g = HKGraph.current(); return g && g.view.nodes.length > 5 && g.view.alpha < 0.05; }), 12000);
  await p.evaluate(() => { const v = HKGraph.current().view; v.user(); v.fit(true); }); await sleep(400);
}
// Knoten nach Titel: Bildschirm-Position, Radius (Welt) und mittlere Deckkraft im oberen Halbring knapp ausserhalb
const probe = (p, t) => p.evaluate((t) => {
  const v = HKGraph.current().view, n = v.nodes.find((n) => n.title === t); if (!n) return null;
  const c = v.c, r = c.getBoundingClientRect(), dpr = v.dpr, ctx = c.getContext('2d');
  const sx = v.tx + n.x * v.k, sy = v.ty + n.y * v.k, rs = v.rr(n) * v.k;
  let sum = 0, cnt = 0;
  for (let a = Math.PI * 1.1; a < Math.PI * 1.9; a += 0.08) {
    for (let d = rs + 2.5; d <= rs + 7; d += 1.5) {
      const px = Math.round((sx + Math.cos(a) * d) * dpr), py = Math.round((sy + Math.sin(a) * d) * dpr);
      sum += ctx.getImageData(px, py, 1, 1).data[3]; cnt++;
    }
  }
  return { x: r.left + sx, y: r.top + sy, rad: v.rad(n), rs: rs, ring: sum / cnt, hits: n.hits || 0, pz: n.pz || 0, pr: n.pr || 0, mf: n.mf || 0 };
}, t);
const vs = (p) => p.evaluate(() => { const v = HKGraph.current().view; return { raf: v.raf, npulse: v.npulse, alpha: v.alpha, k: v.k }; });
async function recall(text) {
  const r = await fetch(BASE + '/api/v1/memory/recall', { method: 'POST', headers: { Authorization: 'Bearer ' + R.token, 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
  return { s: r.status, d: await r.json().catch(() => ({})) };
}
let R = null;
(async () => {
  const br = await PW.chromium.launch();
  try {
    R = rig('setup');
    ok('Rig: Test-Gedaechtnis angelegt', R && R.db && R.token, R);
    const p = await session(br, E.SAGA_USER, E.SAGA_PASS);
    const link = (id) => ({ type: 'paragraph', data: { text: '<a href="/notes#' + id + '">x</a>' } });
    for (const k of Object.keys(R.pages)) {
      const ids = Object.keys(R.topics).filter((t) => R.topics[t] === k).map((t) => R.rows[t]);
      await p.api('PATCH', '/api/notes/' + R.pages[k], { format: 'blocks', body: JSON.stringify({ blocks: ids.map(link) }) });
    }
    await sleep(2500);
    await openGraph(p);
    // Ausschnitt um die Gedaechtnis-Datenbank (fuer das Glow-Bild)
    const dbXY = await p.evaluate((id) => { const v = HKGraph.current().view, n = v.byId(id), r = v.c.getBoundingClientRect(); return n && { x: r.left + v.tx + n.x * v.k, y: r.top + v.ty + n.y * v.k }; }, R.db);
    const zoomIn = async () => { await p.evaluate((id) => { const v = HKGraph.current().view, n = v.byId(id); v.center(n, 2.2, true); }, R.db); await sleep(300); };
    if (E.VORHER) {
      await shot(p, 'graph-hell');
      await zoomIn(); await shot(p, 'graph-hell-nah');
      const d = await session(br, E.SAGA_USER, E.SAGA_PASS, { colorScheme: 'dark' });
      await openGraph(d); await shot(d, 'graph-dunkel');
      await d.evaluate((id) => { const v = HKGraph.current().view, n = v.byId(id); v.center(n, 2.2, true); }, R.db); await sleep(300); await shot(d, 'graph-dunkel-nah');
      ok('Vorher-Bilder', true);
      return;
    }
    const data = await p.evaluate(() => fetch('/api/graph', { headers: { 'X-Saga': '1' } }).then((r) => r.json()));
    ok('/api/graph liefert mem (eigene Datenbank, Zeilen mit Zaehlern)', data.mem && data.mem.db === R.db && data.mem.rows.length === 10, data.mem);
    const kaf = await probe(p, 'Kaffee'), sau = await probe(p, 'Sauerteig'), kom = await probe(p, 'Kompost'), tom = await probe(p, 'Tomaten');
    ok('Groesse waechst mit Abrufen: 30 Abrufe = x1,8 gegenueber nie abgerufen (gleicher Grad)', kaf && sau && Math.abs(kaf.rad / sau.rad - 1.8) < 0.02, [kaf, sau]);
    ok('1 Abruf: maessig groesser (x1,18)', kom && Math.abs(kom.rad / sau.rad - (1 + 0.8 * Math.log(2) / Math.log(21))) < 0.02, kom && kom.rad / sau.rad);
    ok('Nachleuchten sichtbar: Hof um kuerzlich abgerufene Erinnerung, nicht um nie abgerufene', kaf.ring > sau.ring + 40 && tom.ring > sau.ring + 40, [kaf.ring, tom.ring, sau.ring]);
    ok('vor 40 h abgerufen: kein Leuchten mehr', kom.ring < sau.ring + 15, [kom.ring, sau.ring]);
    const lg = await p.textContent('.gr-legend');
    ok('Legende "Kürzlich abgerufen"', /Kürzlich abgerufen/.test(lg), lg);
    await shot(p, 'graph-hell');
    await zoomIn(); await shot(p, 'graph-hell-nah');
    await p.evaluate(() => { const v = HKGraph.current().view; v.setSel(v.nodes.find((n) => n.title === 'Kaffee'), 'key'); }); await sleep(200);
    const cs = await p.textContent('.gr-card-s');
    ok('Karte zeigt die Zahl der Abrufe', /30 Abrufe/.test(cs), cs);
    await p.keyboard.press('Escape'); await sleep(150);
    // Puls nach recall (REST): Live-Ereignis, Zaehler ohne Neuladen
    const h0 = (await probe(p, 'Zugfahrt')).hits;
    const rc = await recall('Nachtzug Wien Flug bequem');
    ok('REST recall trifft die Zugfahrt', rc.s === 200 && rc.d.treffer.some((x) => x.id === R.rows.Zugfahrt), rc);
    const pl = await until(async () => { const z = await probe(p, 'Zugfahrt'); return z.pz ? z : null; }, 5000);
    const st1 = await vs(p);
    ok('Puls: Knoten pulst in der offenen Ansicht (Animation laeuft)', pl && pl.pz > 0 && st1.npulse > 0 && st1.raf, [pl, st1]);
    await sleep(350); await shot(p, 'graph-puls');
    ok('Puls: Zaehler +1 ohne Neuladen', pl && pl.hits === h0 + 1, [h0, pl && pl.hits]);
    await sleep(2200);
    const st2 = await vs(p);
    ok('Puls endet nach ~2 s (keine Dauerschleife)', !st2.npulse && !st2.raf, st2);
    // Schalter
    if (await p.$eval('.gr-panel', (x) => x.hidden)) await p.click('.gr-panel-btn');
    await p.evaluate(() => { [].forEach.call(document.querySelectorAll('.gr-sec'), (d) => { d.open = true; }); });
    const sw = p.locator('.gr-check', { hasText: 'Gedächtnis hervorheben' });
    ok('Schalter "Gedächtnis hervorheben" sichtbar und an', await sw.isVisible() && await sw.locator('input').isChecked());
    await sw.click(); await sleep(500);
    const off = await probe(p, 'Kaffee'), offL = await p.textContent('.gr-legend');
    ok('Schalter aus: keine Groesse/kein Leuchten, Legende ohne Eintrag', off.mf === 0 && Math.abs(off.rad - sau.rad) < 0.01 && !/Kürzlich abgerufen/.test(offL), [off, offL]);
    await p.click('.gr-panel-btn');
    await shot(p, 'graph-schalter-aus');
    const pref = await p.evaluate(() => JSON.parse(localStorage.getItem('saga-graph') || HK.store('saga-graph') || '{}').mem);
    ok('Schalter gespeichert (prefs mem=false)', pref === false, pref);
    await p.keyboard.press('Escape'); await sleep(200);
    await openGraph(p);
    ok('nach erneutem Oeffnen weiter aus', (await probe(p, 'Kaffee')).mf === 0);
    if (await p.$eval('.gr-panel', (x) => x.hidden)) await p.click('.gr-panel-btn');
    await p.evaluate(() => { [].forEach.call(document.querySelectorAll('.gr-sec'), (d) => { d.open = true; }); });
    await p.locator('.gr-check', { hasText: 'Gedächtnis hervorheben' }).click(); await sleep(400);
    ok('wieder an', (await probe(p, 'Kaffee')).mf === 1);
    await p.click('.gr-panel-btn');
    ok('keine Seitenfehler (hell)', !p.errs.length, p.errs);
    // weniger Bewegung: ruhiger Ring statt Puls, keine Animationsschleife
    const r = await session(br, E.SAGA_USER, E.SAGA_PASS, { reducedMotion: 'reduce' });
    await openGraph(r);
    await recall('Tomaten Sonne Wasser');
    const rp = await until(async () => { const z = await probe(r, 'Tomaten'); return z.pr ? z : null; }, 5000);
    await sleep(120);
    const rst = await vs(r);
    ok('prefers-reduced-motion: ruhiger Ring, kein Puls, keine Animationsschleife', rp && rp.pr > 0 && !rp.pz && !rst.raf && !rst.npulse, [rp, rst]);
    await r.evaluate((id) => { const v = HKGraph.current().view, n = v.byId(id); v.center(n, 2.2, true); }, R.db); await sleep(200);
    await shot(r, 'graph-ruhig');
    await sleep(4300);
    ok('prefers-reduced-motion: Ring verschwindet nach 4 s', !(await probe(r, 'Tomaten')).pr);
    ok('keine Seitenfehler (weniger Bewegung)', !r.errs.length, r.errs);
    await r.context().close();
    // dunkel
    const d = await session(br, E.SAGA_USER, E.SAGA_PASS, { colorScheme: 'dark' });
    await openGraph(d);
    const dk = await probe(d, 'Kaffee'), ds = await probe(d, 'Sauerteig');
    ok('dunkel: Nachleuchten sichtbar', dk && ds && dk.ring > ds.ring + 40, [dk, ds]);
    await shot(d, 'graph-dunkel');
    await d.evaluate((id) => { const v = HKGraph.current().view, n = v.byId(id); v.center(n, 2.2, true); }, R.db); await sleep(300);
    await shot(d, 'graph-dunkel-nah');
    ok('keine Seitenfehler (dunkel)', !d.errs.length, d.errs);
    await d.context().close();
    // Konto ohne Gedaechtnis: kein Schalter, keine Legende, kein Feld
    if (E.SAGA_USER2 && E.SAGA_PASS2) {
      const b = await session(br, E.SAGA_USER2, E.SAGA_PASS2);
      await b.api('POST', '/api/notes', { title: 'Seite ohne Gedächtnis' });
      const bd = await b.evaluate(() => fetch('/api/graph', { headers: { 'X-Saga': '1' } }).then((r) => r.json()));
      ok('zweites Konto ohne Gedaechtnis: kein Feld mem', !('mem' in bd), Object.keys(bd));
      await b.goto(BASE + '/notes?t=' + Date.now()); await b.waitForSelector('#pg-graph-btn'); await sleep(600);
      await b.click('#pg-graph-btn'); await b.waitForSelector('.gr-full'); await sleep(1500);
      if (await b.$eval('.gr-panel', (x) => x.hidden)) await b.click('.gr-panel-btn');
      await b.evaluate(() => { [].forEach.call(document.querySelectorAll('.gr-sec'), (d) => { d.open = true; }); });
      ok('zweites Konto: Schalter ausgeblendet', !(await b.locator('.gr-check', { hasText: 'Gedächtnis hervorheben' }).isVisible()));
      ok('zweites Konto: keine Seitenfehler', !b.errs.length, b.errs);
      await b.context().close();
    }
  } catch (e) { ok('Ablauf', false, String(e && e.stack || e).slice(0, 600)); }
  finally {
    try { rig('clean'); } catch (e) { /* egal */ }
    await br.close();
  }
  const n = res.filter(Boolean).length;
  console.log(`\nBROWSER-GRAPH-GEDAECHTNIS ${n}/${res.length} - ` + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
