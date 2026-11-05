const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
// Seitenleiste wie Notion (Web, breite Fenster): ein/aus, schwebt zugeklappt beim Ueberfahren herein, Handy-Schublade bleibt.
//   SAGA_BASE=http://127.0.0.1:8099 SAGA_USER=konto SAGA_PASS=... [PLAYWRIGHT=...] node tests/browser_seitenleiste.js [Bilder-Ordner]
const SHOTS = process.argv[2] || '';
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1400, height: 860 } });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(BASE + '/login');
  await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  await p.goto(BASE + '/notes'); await p.waitForTimeout(1500);
  const st = () => p.evaluate(() => { const s = document.getElementById('pg-side'), pg = document.getElementById('pages'); const r = s.getBoundingClientRect(), cs = getComputedStyle(s);
    return { body: document.body.className, cols: getComputedStyle(document.querySelector('.np-main')).marginLeft, x: Math.round(r.x), w: Math.round(r.width), op: cs.opacity, vis: cs.visibility, pos: cs.position }; });
  const out = [];
  out.push(['start', await st()]);
  if (SHOTS) await p.screenshot({ path: SHOTS + '/1-offen.png' });
  // Zuklappen und Breite waehrend der Animation messen
  const trace = p.evaluate(() => new Promise(res => { const pg = document.getElementById('page'); const t0 = performance.now(); const a = [];
    (function f() { a.push([Math.round(performance.now() - t0), Math.round(pg.getBoundingClientRect().x)]); if (performance.now() - t0 < 400) requestAnimationFrame(f); else res(a); })(); }));
  await p.click('#pg-side-btn');
  out.push(['zu-verlauf', (await trace).filter((x, i, a) => i == 0 || x[1] !== a[i - 1][1])]);
  await p.mouse.move(800, 500); await p.waitForTimeout(400);
  out.push(['zu', await st()]);
  if (SHOTS) await p.screenshot({ path: SHOTS + '/2-zu.png' });
  // Knopf ueberfahren -> schwebt herein
  await p.hover('#pg-side-btn'); await p.waitForTimeout(350);
  out.push(['peek-knopf', await st()]);
  if (SHOTS) await p.screenshot({ path: SHOTS + '/3-peek.png' });
  // in die Karte, dann raus
  await p.mouse.move(100, 300); await p.waitForTimeout(400);
  out.push(['in-karte', await st()]);
  await p.mouse.move(900, 400); await p.waitForTimeout(600);
  out.push(['weg', await st()]);
  // linker Rand
  await p.mouse.move(3, 400); await p.waitForTimeout(350);
  out.push(['rand', await st()]);
  // Anheften per Klick
  await p.click('#pg-side-btn'); await p.waitForTimeout(400);
  out.push(['angeheftet', await st()]);
  if (SHOTS) await p.screenshot({ path: SHOTS + '/4-wieder-offen.png' });
  // Tastenkuerzel
  await p.keyboard.press('Control+Backslash'); await p.waitForTimeout(400);
  out.push(['kuerzel-zu', await st()]);
  // schmal (Handy): Schublade unveraendert
  await p.setViewportSize({ width: 500, height: 800 }); await p.waitForTimeout(300);
  out.push(['schmal', await st()]);
  for (const [k, v] of out) console.log(k, JSON.stringify(v));
  const S = Object.fromEntries(out), bad = [];
  const ok = (c, m) => { if (!c) bad.push(m); };
  ok(S.start.vis === 'visible' && S.start.x === 0 && S.start.cols === '270px', 'Start: Leiste offen, Inhalt daneben');
  ok(S.zu.vis === 'hidden' && S.zu.cols === '0px', 'Zugeklappt: Inhalt volle Breite, Leiste unsichtbar');
  ok(S['peek-knopf'].vis === 'visible' && S['peek-knopf'].x === 0 && S['peek-knopf'].pos === 'fixed', 'Knopf ueberfahren: Karte schwebt herein');
  ok(S['peek-knopf'].cols === S.zu.cols, 'Schweben verschiebt den Inhalt nicht');
  ok(S['in-karte'].vis === 'visible', 'Maus in der Karte: bleibt offen');
  ok(S.weg.vis === 'hidden', 'Maus weg: Karte verschwindet');
  ok(S.rand.vis === 'visible', 'Linker Rand: Karte schwebt herein');
  ok(S.angeheftet.vis === 'visible' && S.angeheftet.cols === '270px' && !S.angeheftet.body.includes('side-peek'), 'Klick heftet an');
  ok(S['kuerzel-zu'].vis === 'hidden', 'Strg+\\ klappt zu');
  ok(S.schmal.x < 0, 'Handy: Schublade bleibt zu');
  ok(!errs.length, 'keine Seitenfehler: ' + errs.join(' | '));
  console.log(bad.length ? 'FEHLER:\n- ' + bad.join('\n- ') : 'ALLES GRUEN');
  await b.close();
  process.exit(bad.length ? 1 : 0);
})();
