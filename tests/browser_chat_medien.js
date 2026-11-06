// Bildansicht im Chat (2.5 medien1, static/chat.js viewImage) ohne Server: der echte Abschnitt "Original in gross" bis
// save() aus static/chat.js plus overlay()/btn() wird mit kleinen Stubs (Entschluesseln = durchreichen) in eine Testseite
// gebaut und in headless Chromium geprueft: Start beim angeklickten Bild, Zaehler "n / m" ueber alle Bilder des Chats
// (Nicht-Bilder uebersprungen), Pfeiltasten und Knoepfe, Zoom per Rad, Ziehen verschiebt, Doppelklick an/aus, Taste 0,
// Wischen, Herunterladen, Bild kopieren (image/png in der Zwischenablage, JPEG wird umgewandelt), Esc schliesst und
// raeumt die Tasten ab; Handy (Touch): Spreizen zoomt, Wischen blaettert. Keine Seitenfehler. Die Seite kommt ueber
// page.route von einem https-Ursprung (sicherer Kontext, sonst fehlt navigator.clipboard).
//   node tests/browser_chat_medien.js            [SHOTS=/pfad]  (PLAYWRIGHT=playwright-core o. ae. moeglich)
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const fs = require('fs'), path = require('path');
const ROOT = path.dirname(__dirname), ORIGIN = 'https://saga-medien.test', SHOTS = process.env.SHOTS || '';
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
const src = fs.readFileSync(path.join(ROOT, 'static', 'chat.js'), 'utf8');
if (src.indexOf('  // Original in groß (erst hier geladen).') < 0) { console.error('Abschnitt viewImage in static/chat.js nicht gefunden'); process.exit(2); }
const a = src.indexOf('  // Original in groß (erst hier geladen).'), b = src.indexOf('  function save(url, name) {');
const ovA = src.indexOf('  var ov = null;'), ovB = src.indexOf('  function btn(label');
const btnLine = src.slice(ovB, src.indexOf('\n', ovB));
const css = fs.readFileSync(path.join(ROOT, 'static', 'tokens.css'), 'utf8') + fs.readFileSync(path.join(ROOT, 'static', 'style.css'), 'utf8') + fs.readFileSync(path.join(ROOT, 'static', 'chat.css'), 'utf8');
const js = `(function(){
  var T = function (s, o) { return String(s).replace(/\\{(\\w+)\\}/g, function (_, k) { return o && k in o ? o[k] : _; }); };
  function el(t, c, x) { var e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; }
  var HK = window.HK = { menuOpen: function () { return false; }, toast: function (m) { window.__toasts.push(m); } };
  window.__toasts = [];
  var IMG = /^image\\/(png|jpeg|gif|webp)$/, VID = null, BIG_VIEW_MAX = 100 * 1048576;
  var BLOBS = window.__blobs = {};
  var S = window.__S = { cur: { id: 1 }, msgs: [] };
  function fileCid(c, m) { return c.id; }
  function memGet() { return null; }
  function getBlob(f) { return new Promise(function (r) { setTimeout(function () { r(BLOBS[f.id]); }, 30); }); }
  function run(op, d) { return Promise.resolve({ plain: d.blob }); }
  function saveBig() {}
  ${src.slice(ovA, ovB)}
  ${btnLine}
  ${src.slice(a, b)}
  function save(url, name) { window.__saved = name; }
  window.__view = viewImage;
})();`;
const PAGE = `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body><script>${js}</script></body></html>`;
(async () => {
  const br = await chromium.launch();
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 } });
  await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: ORIGIN });
  await ctx.route(ORIGIN + '/**', rt => rt.fulfill({ contentType: 'text/html; charset=utf-8', body: PAGE }));
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto(ORIGIN + '/page.html');
  const res = []; const ok = (n, c, i) => { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); };
  await p.evaluate(async () => {
    function mk(color) { const cv = document.createElement('canvas'); cv.width = 800; cv.height = 600; const g = cv.getContext('2d'); g.fillStyle = color; g.fillRect(0, 0, 800, 600); g.fillStyle = '#fff'; g.fillRect(380, 280, 40, 40);
      return new Promise(r => cv.toBlob(b => b.arrayBuffer().then(r), 'image/jpeg', 0.9)); }
    const cols = ['#c33', '#3c3', '#33c'];
    for (let i = 0; i < 3; i++) window.__blobs['f' + i] = await mk(cols[i]);
    window.__S.msgs = [
      { kind: 'msg', body: { text: 'a', files: [{ id: 'f0', n: 'eins.jpg', m: 'image/jpeg', k: 'k' }] } },
      { kind: 'msg', body: { text: '', files: [{ id: 'x', n: 'doc.pdf', m: 'application/pdf', k: 'k' }] } },
      { kind: 'msg', body: { text: '', files: [{ id: 'f1', n: 'zwei.jpg', m: 'image/jpeg', k: 'k' }, { id: 'f2', n: 'drei.jpg', m: 'image/jpeg', k: 'k' }] } },
    ];
    const f = window.__S.msgs[2].body.files[0];
    window.__view(1, f, f.n);
  });
  await p.waitForSelector('.ct-gal img[src^="blob:"]');
  const st = () => p.evaluate(() => ({ title: document.querySelector('.ct-ov h2').textContent, n: document.querySelector('.ct-gal-n').textContent,
    tf: document.querySelector('.ct-gal img').style.transform, prevDis: document.querySelector('.ct-gal-prev').disabled, nextDis: document.querySelector('.ct-gal-next').disabled,
    share: !document.querySelector('.ct-gal-share').hidden, copy: !document.querySelector('.ct-gal-copy').hidden, open: !!document.querySelector('.ct-ov') }));
  let s = await st();
  ok('startet beim angeklickten Bild, Zaehler 2 / 3', s.title === 'zwei.jpg' && s.n === '2 / 3', s);
  ok('Kopieren sichtbar (Chromium), Teilen je nach canShare', s.copy, s);
  await p.keyboard.press('ArrowRight'); await p.waitForTimeout(150); s = await st();
  ok('Pfeil rechts -> 3 / 3, weiter deaktiviert', s.n === '3 / 3' && s.title === 'drei.jpg' && s.nextDis, s);
  await p.click('.ct-gal-prev'); await p.click('.ct-gal-prev'); await p.waitForTimeout(150); s = await st();
  ok('Knopf zurueck 2x -> 1 / 3, zurueck deaktiviert', s.n === '1 / 3' && s.prevDis, s);
  await p.waitForSelector('.ct-gal img[src^="blob:"]');
  const box = await p.locator('.ct-gal').boundingBox(), cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  await p.mouse.move(cx + 100, cy + 50); await p.mouse.wheel(0, -400); await p.waitForTimeout(100); s = await st();
  ok('Rad zoomt hinein', /scale\((?!1\.000)/.test(s.tf), s);
  await p.mouse.move(cx, cy); await p.mouse.down(); await p.mouse.move(cx - 80, cy - 40, { steps: 5 }); await p.mouse.up(); await p.waitForTimeout(50);
  const s2 = await st(); ok('Ziehen verschiebt beim Zoom', s2.tf !== s.tf && s2.n === '1 / 3', [s.tf, s2.tf]);
  await p.mouse.dblclick(cx, cy); await p.waitForTimeout(50); s = await st();
  ok('Doppelklick setzt zurueck', s.tf === '', s);
  await p.mouse.dblclick(cx, cy); await p.waitForTimeout(50); s = await st();
  ok('Doppelklick zoomt 2,5x', /scale\(2\.5\)/.test(s.tf), s);
  await p.keyboard.press('0'); s = await st(); ok('Taste 0 setzt zurueck', s.tf === '', s);
  // Wischen (Maus ohne Zoom genuegt fuer die Logik)
  await p.mouse.move(cx + 150, cy); await p.mouse.down(); await p.mouse.move(cx - 150, cy, { steps: 6 }); await p.mouse.up(); await p.waitForTimeout(150); s = await st();
  ok('Wischen nach links -> naechstes Bild', s.n === '2 / 3', s);
  await p.waitForSelector('.ct-gal img[src^="blob:"]');
  await p.click('.ct-view-save'); ok('Herunterladen', await p.evaluate(() => window.__saved) === 'zwei.jpg');
  await p.bringToFront(); await p.click(".ct-gal-copy"); await p.waitForTimeout(2500); 
  const clip = await p.evaluate(async () => { try { const it = await navigator.clipboard.read(); return it[0].types; } catch (e) { return 'err ' + e.message; } });
  ok('Bild kopieren -> image/png in der Zwischenablage', Array.isArray(clip) && clip.includes('image/png'), [clip, await p.evaluate(() => window.__toasts)]);
  if (SHOTS) await p.screenshot({ path: path.join(SHOTS, 'medien-viewer.png') });
  await p.keyboard.press('Escape'); await p.waitForTimeout(50);
  ok('Esc schliesst', !(await p.evaluate(() => !!document.querySelector('.ct-ov'))));
  await p.keyboard.press('ArrowRight');
  ok('keine Seitenfehler', errs.length === 0, errs);
  // Touch: Pinch ueber CDP
  const ctx2 = await br.newContext({ viewport: { width: 390, height: 800 }, hasTouch: true, isMobile: true });
  await ctx2.route(ORIGIN + '/**', rt => rt.fulfill({ contentType: 'text/html; charset=utf-8', body: PAGE }));
  const q = await ctx2.newPage(); q.on('pageerror', e => errs.push(e.message));
  await q.goto(ORIGIN + '/page.html');
  await q.evaluate(async () => {
    const cv = document.createElement('canvas'); cv.width = 400; cv.height = 300; cv.getContext('2d').fillRect(0, 0, 400, 300);
    const buf = await new Promise(r => cv.toBlob(b => b.arrayBuffer().then(r), 'image/png'));
    window.__blobs.a = buf; window.__blobs.b = buf;
    window.__S.msgs = [{ kind: 'msg', body: { files: [{ id: 'a', n: 'a.png', m: 'image/png' }, { id: 'b', n: 'b.png', m: 'image/png' }] } }];
    window.__view(1, window.__S.msgs[0].body.files[0], 'a.png');
  });
  await q.waitForSelector('.ct-gal img[src^="blob:"]');
  const bb = await q.locator('.ct-gal').boundingBox(), mx = bb.x + bb.width / 2, my = bb.y + bb.height / 2;
  const cdp = await ctx2.newCDPSession(q);
  const tp = (type, pts) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: pts.map((p, i) => ({ x: p[0], y: p[1], id: i })) });
  await tp('touchStart', [[mx - 20, my], [mx + 20, my]]);
  for (let i = 1; i <= 5; i++) await tp('touchMove', [[mx - 20 - i * 15, my], [mx + 20 + i * 15, my]]);
  await tp('touchEnd', []);
  await q.waitForTimeout(100);
  const tf = await q.evaluate(() => document.querySelector('.ct-gal img').style.transform);
  ok('Spreizen (Touch) zoomt', /scale\((?!1\.000)/.test(tf), tf);
  await q.evaluate(() => { document.querySelector('.ct-gal img').style.transform = ''; });
  await q.keyboard.press('0');
  await tp('touchStart', [[mx + 120, my]]); for (let i = 1; i <= 6; i++) await tp('touchMove', [[mx + 120 - i * 40, my]]); await tp('touchEnd', []);
  await q.waitForTimeout(150);
  ok('Wischen (Touch) -> 2 / 2', (await q.evaluate(() => document.querySelector('.ct-gal-n').textContent)) === '2 / 2');
  if (SHOTS) await q.screenshot({ path: path.join(SHOTS, 'medien-viewer-handy.png') });
  ok('keine Seitenfehler (gesamt)', errs.length === 0, errs);
  await br.close();
  console.log(res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'); process.exit(res.every(Boolean) ? 0 : 1);
})();
