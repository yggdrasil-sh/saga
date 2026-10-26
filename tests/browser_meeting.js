// Browser-Ende-zu-Ende Meeting-Ansicht (MEETING-SPEC.md, Lieferung 1/2, static/meeting.js + static/calls.js) - drei
// Playwright-Kontexte (je ein eigener Chromium) mit Kamera/Mikrofon-Attrappe: Anna mit zwei Geraeten (A = Hauptgeraet,
// A2 = Begleitgeraet), Ben (B, 1280x720). Laeuft gegen einen Test-Stack wie tests/browser_raum.js (Mesh, ohne Knoten):
//   SAGA_BASE=http://host:port SAGA_USER=anna SAGA_PASS=... SAGA_USER2=ben SAGA_PASS2=... [SHOTS=/pfad] [SHOT_PREFIX=meet-web-] \
//   [PLAYWRIGHT=...] [PW_CHANNEL=chromium | CHROME=/usr/bin/chromium] node tests/browser_meeting.js
// WebRTC braucht eine Netz-Schnittstelle ausser lo. Geprueft (Szenarien S1-S3, S6, A1, A3, S14 aus §12):
// - S2: A2 tritt als Begleitgeraet bei -> Ben sieht EINE Kachel Anna (Einzelansicht, kein Zaehler "3 Geraete"); Anna (A)
//   sieht A2 nicht als Kachel, sondern als Chip "… verbunden" im Kopf
// - S3: A2 teilt den Bildschirm -> Ben: Auto = Praesentation, Buehne "Bildschirm von Anna (A2-Geraet)" (contain, ohne
//   Kamera-Bild-im-Bild), Annas Kamera als Kachel im Streifen, kein Banner oben, Hinweis-Chip unten links mit x
// - A1: nach 4 s ohne Bedienung sind Kopf, Knoepfe, Chip und Namensleiste der Buehne weg, Mausbewegung holt sie zurueck
// - geteilter Inhalt >= 70 % der Fensterflaeche bei 1280x720 mit ausgeblendeten Leisten (Messung der contain-Flaeche)
// - Layout-Knopf (Menue, Galerie/Auto, gespeichert in saga-call-layout), Anheften (Antippen im Streifen, Esc loest),
//   Zoom Strg +/0, Kuerzel M, Kuerzel-Liste ?, Vollbild-Knopf vorhanden
// - S1: eigenes Bild schwebend im Seitenverhaeltnis der Kamera (Einzelansicht), in der Praesentation als Kachel im Streifen
// - Ende des Teilens -> zurueck zur Einzelansicht; A3: Handy-Breite ohne waagrechtes Scrollen
// - Screenshots hell und dunkel, Desktop und Handy (SHOTS)
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_USER2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '', PFX = E.SHOT_PREFIX || 'meet-web-';
const res = [], PERF = {};
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, PFX + name + '.png') }); } }
async function until(fn, ms = 20000, step = 150) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const sleep = ms => new Promise(r => setTimeout(r, ms));

// Bildschirm-Attrappe: Farbbalken 1280x720 aus einem Canvas (16:9, gut erkennbar), statt der Auswahl des Browsers
const SCREEN_HOOK = () => {
  try { localStorage.setItem('saga-screen-hint', '0'); } catch (e) { /* egal */ }
  const md = navigator.mediaDevices; if (!md) return;
  md.getDisplayMedia = async () => {
    const c = document.createElement('canvas'); c.width = 1280; c.height = 720;
    const g = c.getContext('2d'), cols = ['#e6e6e6', '#e6e600', '#00e6e6', '#00e600', '#e600e6', '#e60000', '#0000e6'];
    let n = 0;
    setInterval(() => { cols.forEach((x, i) => { g.fillStyle = x; g.fillRect(i * c.width / 7, 0, c.width / 7 + 1, c.height); }); g.fillStyle = '#000'; g.font = '48px sans-serif'; g.fillText('Demo ' + (n++ % 100), 40, 680); }, 100);
    const s = c.captureStream(15); s.getVideoTracks()[0].contentHint = 'detail';
    return s;
  };
};
const BROWSERS = [];
async function session(user, pass, name, vp) {
  const origin = new URL(BASE).origin;
  const br = await chromium.launch({ ...(E.CHROME ? { executablePath: E.CHROME } : { channel: E.PW_CHANNEL || 'chrome' }), headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin, '--autoplay-policy=no-user-gesture-required'] });
  BROWSERS.push(br);
  const ctx = await br.newContext({ viewport: vp || { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: 'dark' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.__name = name;
  p.on('pageerror', e => console.log('  [pageerror ' + name + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + name + '] ' + m.text()));
  await p.addInitScript(SCREEN_HOOK);
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', pass);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  return p;
}
const RKF = u => path.join(os.tmpdir(), 'saga-browser-rk-' + BASE.replace(/\W+/g, '_') + '-' + u + '.txt');
async function chatKeys(q, user) {
  await q.goto(BASE + '/chats'); await q.waitForTimeout(2500);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim(); fs.writeFileSync(RKF(user), rk, { mode: 0o600 });
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 60000 });
  } else if (await q.$('.ct-rk-go') && fs.existsSync(RKF(user))) {
    await q.click('.ct-rk-go'); await q.fill('.ct-rk-in', fs.readFileSync(RKF(user), 'utf8').trim());
    await q.click('.ct-rk-ok'); await q.waitForSelector('.ct-rk-ok', { state: 'detached', timeout: 60000 });
  }
  await q.waitForTimeout(800);
}
async function liveOk(...ps) { for (const p of ps) await until(() => p.evaluate(() => !!(window.HKLive && window.HKLive.state().sse)), 15000, 200); }
const room = p => p.evaluate(() => window.HKCalls && window.HKCalls.room());
const scr = p => p.evaluate(() => window.HKCalls && window.HKCalls.screen());
const dev = p => p.evaluate(() => window.HKChat.state().device);
const allConn = (r, n) => r && r.links.length === n && r.links.every(l => l.connected) && r;
// Sichtbarkeit der Leisten und Flaeche des geteilten Inhalts (contain) im Verhaeltnis zur Fensterflaeche
const LOOK = () => {
  const w = document.querySelector('.cl-win'), op = s => { const x = w && w.querySelector(s); return x ? +getComputedStyle(x).opacity : -1; };
  const big = w && w.querySelector('.cl-mbig .cl-tile'), v = big && big.querySelector('video');
  let area = 0, rect = null;
  if (v && v.videoWidth) {
    const r = v.getBoundingClientRect(), s = Math.min(r.width / v.videoWidth, r.height / v.videoHeight);
    area = (v.videoWidth * s) * (v.videoHeight * s) / (innerWidth * innerHeight); rect = [Math.round(r.width), Math.round(r.height), v.videoWidth, v.videoHeight];
  }
  const shareBar = w && w.querySelector('.cl-share');
  return { idle: !!w && w.classList.contains('idle'), top: op('.cl-top'), ctl: op('.cl-ctl'), chip: op('.cl-mchip'), stageName: op('.cl-mbig .cl-tl'),
           chipText: (w.querySelector('.cl-mchip:not([hidden]) .cl-mchip-t') || {}).textContent || '', banner: !!shareBar && !shareBar.hidden && shareBar.offsetParent !== null,
           stageVideos: big ? big.querySelectorAll('video').length : 0, pipInScreen: !!w.querySelector('.cl-mbig .cl-tv2, .cl-mbig .cl-remote2'),
           fit: v ? getComputedStyle(v).objectFit : '', area: Math.round(area * 1000) / 1000, rect, stageBg: big ? getComputedStyle(w.querySelector('.cl-mbig')).backgroundColor : '',
           layoutBtn: !!w.querySelector('.cl-ctl .cl-layout'), layoutLabel: (w.querySelector('.cl-ctl .cl-layout') || { getAttribute: () => '' }).getAttribute('aria-label'),
           full: !!w.querySelector('.cl-tb .cl-full'), info: (w.querySelector('.cl-info') || {}).textContent || '',
           scrollX: document.documentElement.scrollWidth > innerWidth + 1 };
};
const CTLFIT = () => { const bs = [...document.querySelectorAll('.cl-win .cl-ctl > .cl-btn')].map(b => b.getBoundingClientRect());
  const red = document.querySelector('.cl-win .cl-ctl .cl-red'), rr = red && red.getBoundingClientRect();
  return { n: bs.length, inside: bs.every(r => r.left >= 0 && r.right <= innerWidth + 0.5), oneRow: bs.every(r => Math.abs(r.top - bs[0].top) < 2),
           red: !!rr && rr.left >= 0 && rr.right <= innerWidth && rr.width >= 44, more: !!document.querySelector('.cl-win .cl-ctl .cl-more') }; };
const look = p => p.evaluate(LOOK);
const selfBox = p => p.evaluate(() => { const v = document.querySelector('.cl-local'); if (!v || v.hidden || getComputedStyle(v).display === 'none') return null;
  const r = v.getBoundingClientRect(); return { w: r.width, h: r.height, vw: v.videoWidth, vh: v.videoHeight }; });

// Ablauf ueber einen Knoten (calls.horn_all, Testlabor): 1:1 A <-> B, A2 als Begleitgeraet, A2 teilt den Bildschirm.
// Geprueft: Ben sieht EINE Kachel Anna (Personen ueber who), Anna sieht A2 nur als Chip, Bildschirm gross mit Hinweis-Chip
// unten links (x) statt Banner, Leisten blenden aus, Echo-Blatt auf A2.
const horn = p => p.evaluate(() => window.HKCalls && window.HKCalls.horn && window.HKCalls.horn());
async function hornFlow(a, a2, b, o) {
  INFO_HORN = true;
  await a.goto(BASE + '/chats#u' + encodeURIComponent(o.U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1000);
  await b.goto(BASE + '/chats'); await a2.goto(BASE + '/chats'); await liveOk(a, b, a2);
  await a.click('#ct-call-v');
  await b.waitForSelector('.cl-ring', { timeout: 20000 }); await b.click('.cl-ring .cl-big');
  const h1 = await until(async () => { const x = await horn(a), y = await horn(b), r = await room(b);
    return (x && x.up && y && y.up && [x, y]) || (r && !y && r.links.length && r.links.every(l => l.connected) && 'mesh'); }, 40000);
  if (h1 === 'mesh') {
    console.log('  (Server nimmt fuer das 1:1 keinen Knoten - weiter mit dem Mesh-Ablauf)');
    await b.click('.cl-ctl .cl-red').catch(() => {});
    await until(async () => !(await room(a)) && !(await room(b)), 15000); await sleep(1500);
    return false;
  }
  ok('Knoten: 1:1 A <-> B verbunden', h1, [await horn(a), await horn(b)]);
  await until(() => a2.evaluate(() => !!document.querySelector('.cl-run')), 20000, 250);
  await a2.click('.cl-run .cl-pill:not(.cl-run-go)');
  const h2 = await until(async () => { const x = await horn(a2), r = await room(b); return x && x.up && r && r.legs.length === 3 && x; }, 40000);
  ok('Knoten: A2 als Begleitgeraet verbunden (drei Geraete im Anruf)', h2, [await horn(a2), await room(b)]);
  await sleep(2500);
  const hb = await horn(b), lb = await look(b);
  ok('S2 (Knoten): Ben sieht EINE Kachel Anna, kein Zaehler "3 Geräte"', hb.tiles.length === 1 && hb.tiles[0].dev === o.dA && !/3 Geräte/.test(lb.info), [hb.tiles, lb.info]);
  const ha = await horn(a), la = await look(a);
  ok('§4.2 (Knoten): Anna sieht A2 nicht als Kachel, sondern als Chip "… verbunden"', ha.tiles.length === 1 && ha.tiles[0].dev === o.dB && /verbunden/.test(la.info), [ha.tiles, la.info]);
  const left = await Promise.all([a, b].map(p => p.evaluate(() => document.querySelectorAll('.cl-mtile').length + (document.querySelector('.cl-win.meet') ? 100 : 0))));
  ok('Knoten: keine Kacheln der Mesh-Ansicht ueber der Knoten-Ansicht', left.every(n => n === 0), left);
  await shot(b, 'knoten-1-einzel-dunkel');
  await a2.mouse.move(300, 300);
  await a2.click('.cl-ctl .cl-round[aria-label="Mikrofon einschalten"]');
  const sheet = await until(() => a2.evaluate(() => { const d = document.querySelector('.cl-echo'); return d && d.textContent; }), 4000);
  ok('§4.3 (Knoten): Echo-Blatt auf dem Begleitgeraet', sheet && /im selben Raum/.test(sheet), sheet);
  if (sheet) await a2.click('.cl-echo-no');
  await a2.click('.cl-ctl .cl-round[aria-label="Bildschirm teilen"]');
  const sh = await until(async () => { const x = await horn(b), w = await b.evaluate(() => document.querySelector('.cl-win').className); return x.shared && x.shared.dev === o.dA2 && x.shared.live && /horn-scr/.test(w) && x; }, 30000);
  ok('S3 (Knoten): Bildschirm des Begleitgeraets gross bei Ben', sh, await horn(b));
  const cap = await b.evaluate(() => { const s = document.querySelector('.cl-share'); return s && !s.hidden ? { t: s.textContent, cls: s.className, x: !!s.querySelector('.cl-mchip-x') } : null; });
  ok('S3 (Knoten): Hinweis-Chip "… teilt den Bildschirm (Geraet)" mit x, keine eigene Kachel fuer A2', cap && /teilt den Bildschirm \(/.test(cap.t) && /cap/.test(cap.cls) && cap.x
     && (await horn(b)).tiles.length === 1, [cap, (await horn(b)).tiles]);
  await shot(b, 'knoten-2-bildschirm-leisten-dunkel');
  await b.mouse.move(640, 360);
  const idle = await until(async () => { const x = await look(b); return x.idle && x.top === 0 && x.ctl === 0 && x; }, 8000);
  ok('A1 (Knoten): Leisten nach 4 s ausgeblendet', idle, await look(b));
  const ar = await b.evaluate(() => { const v = document.querySelector('.cl-win.horn-scr video.cl-hscreen'); if (!v || !v.videoWidth) return 0;
    const r = v.getBoundingClientRect(), s = Math.min(r.width / v.videoWidth, r.height / v.videoHeight); return Math.round(v.videoWidth * s * v.videoHeight * s / (innerWidth * innerHeight) * 1000) / 1000; });
  PERF.flaeche_bildschirm_knoten = ar;
  ok(`S3 (Knoten): geteilter Inhalt nutzt ${Math.round(ar * 100)} % der Fensterflaeche (1280x720, Leisten aus; Ziel >= 70 %)`, ar >= 0.70, ar);
  ok('S3 (Knoten): Bildschirm sichtbar (nichts liegt darueber)', await b.evaluate(() => { const v = document.querySelector('.cl-win.horn-scr video.cl-hscreen'); if (!v) return false;
    const r = v.getBoundingClientRect(), e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return e === v; }));
  ok('§7 (Knoten): eigenes Bild schwebt nicht ueber dem Bildschirm (Kachel im Streifen)', !(await selfBox(b)) && await b.evaluate(() => !!document.querySelector('.cl-grid .cl-me')));
  await shot(b, 'knoten-3-bildschirm-ausgeblendet-dunkel');
  await b.emulateMedia({ colorScheme: 'light' }); await shot(b, 'knoten-3-bildschirm-ausgeblendet-hell'); await b.emulateMedia({ colorScheme: 'dark' });
  await b.setViewportSize({ width: 390, height: 844 }); await sleep(800);
  ok('A3 (Knoten): Handy ohne waagrechtes Scrollen', !(await look(b)).scrollX);
  await b.mouse.move(100, 100); await sleep(300);
  const cfh = await b.evaluate(CTLFIT);
  const above = await b.evaluate(() => { const g = document.querySelector('.cl-win.horn-strip .cl-grid'), c = document.querySelector('.cl-win .cl-ctl > .cl-btn');
    return !g || !c || g.getBoundingClientRect().bottom <= c.getBoundingClientRect().top + 1; });
  ok('Knoten Handy: Leiste einzeilig im Fenster, Auflegen ganz sichtbar, Streifen endet ueber der Leiste', cfh.inside && cfh.oneRow && cfh.red && above, [cfh, above]);
  await shot(b, 'knoten-4-handy-dunkel');
  await b.setViewportSize({ width: 1280, height: 720 });
  await b.mouse.move(600, 300);
  await b.click('.cl-ctl .cl-red');
  ok('Knoten: Auflegen beendet den Anruf', await until(async () => !(await room(a)) && !(await room(a2)), 15000));
  return true;
}
let INFO_HORN = false;

(async () => {
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  try {
    const a = await session(U1, E.SAGA_PASS, 'A'), b = await session(U2, E.SAGA_PASS2, 'B', { width: 1280, height: 720 });
    await chatKeys(a, U1); await chatKeys(b, U2);
    const a2 = await session(U1, E.SAGA_PASS, 'A2');
    await chatKeys(a2, U1);
    const dA = await dev(a), dA2 = await dev(a2), dB = await dev(b);
    ok('Anna mit zwei Geraeten (A, A2), Ben (B) - eigene Chat-Schluessel', dA && dA2 && dB && dA !== dA2, { dA, dA2, dB });
    if ((await a.evaluate(() => fetch('/api/calls/config', { headers: { 'X-Saga': '1' } }).then(r => r.json()).catch(() => ({})))).horn_dm) {
      // calls.horn_all: derselbe Ablauf ueber den Knoten; nimmt der Server fuer das 1:1 doch keinen Knoten, folgt Mesh
      if (await hornFlow(a, a2, b, { dA, dA2, dB, U2 })) return;
    }
    await b.evaluate(() => { try { localStorage.removeItem('saga-call-layout'); localStorage.removeItem('saga-call-self'); } catch (e) { /* egal */ } });

    // --- 1:1 A <-> B, S1: eigenes Bild im Seitenverhaeltnis der Kamera ---------------------------------------------
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1000);
    await b.goto(BASE + '/chats'); await a2.goto(BASE + '/chats'); await liveOk(a, b, a2);
    await a.click('#ct-call-v');
    await b.waitForSelector('.cl-ring', { timeout: 15000 }); await b.click('.cl-ring .cl-big');
    const r1 = await until(async () => { const r = allConn(await room(b), 1); return r && r.links[0].verified && r; }, 25000);
    ok('S1: 1:1 A <-> B verbunden, Einzelansicht', r1 && !r1.mesh, r1);
    const sb1 = await until(async () => { const s = await selfBox(b); return s && s.vw && s; }, 8000);
    ok(`S1: eigenes Bild schwebend im Seitenverhaeltnis der Kamera (${sb1 && Math.round(sb1.w)}x${sb1 && Math.round(sb1.h)}, Kamera ${sb1 && sb1.vw}x${sb1 && sb1.vh})`,
       sb1 && Math.abs(sb1.w / sb1.h - sb1.vw / sb1.vh) < 0.04, sb1);
    await b.dblclick('.cl-local'); await sleep(700);
    const sbL = await selfBox(b);
    ok('S1 (§7): Doppelklick auf das eigene Bild schaltet die Groesse (M -> L, Verhaeltnis bleibt)', sbL && sbL.w > sb1.w * 1.3 && Math.abs(sbL.w / sbL.h - sb1.w / sb1.h) < 0.04, [sb1, sbL]);
    await b.dblclick('.cl-local'); await sleep(400); await b.dblclick('.cl-local'); await sleep(700);
    const sbM = await selfBox(b);
    ok('... L -> S -> M: wieder die Ausgangsgroesse', sbM && Math.abs(sbM.w - sb1.w) < 2, sbM);
    const l1 = await look(b);
    ok('S1: Layout-Knopf im 1:1 ausgeblendet, Vollbild-Knopf da', !l1.layoutBtn && l1.full, l1);
    await shot(b, '1-einzel-dunkel');

    // --- S2: Begleitgeraet ohne Bild ----------------------------------------------------------------------------------
    const bar = await until(async () => a2.evaluate(() => { const x = document.querySelector('.cl-run'); return x && x.textContent; }), 10000);
    ok('A2: Leiste "Anruf läuft auf …"', bar && /Hinzufügen/.test(bar), bar);
    await a2.click('.cl-run .cl-pill:not(.cl-run-go)');
    const r2 = await until(async () => { const r = allConn(await room(a2), 2); return r && r.companion && r; }, 25000);
    ok('A2: als Begleitgeraet verbunden (mit A und B)', r2, await room(a2));
    const rb2 = await until(async () => allConn(await room(b), 2), 15000);
    await sleep(800);
    const rb2b = await room(b), lb2 = await look(b);
    ok('S2: Ben sieht EINE Kachel Anna (Einzelansicht, A2 nirgends als Kachel)', rb2 && !rb2b.mesh && rb2b.tiles === 0 && !(await b.$('.cl-mtile')), rb2b);
    ok('S2: Kopf ohne Geraetezaehler "3 Geräte"', !/3 Geräte/.test(lb2.info), lb2.info);
    const la = await until(async () => { const x = await look(a); return /verbunden/.test(x.info) && x; }, 8000);
    ok('S2 (§4.2): Anna (A) sieht das Begleitgeraet als Chip im Kopf, nicht als Kachel', la && !(await a.$('.cl-mtile')),
       [(await look(a)).info, await a.evaluate(() => { const R = window.HKCalls.room(); return R && { legs: R.legs, mesh: R.mesh, links: R.links.map(l => [l.device, l.me]) }; })]);
    const lc = await a2.evaluate(() => (document.querySelector('.cl-info') || {}).textContent || '');
    ok('§4.2: Kopf auf dem Begleitgeraet "Begleitgerät · Hauptgerät …"', /Begleitgerät · Hauptgerät/.test(lc), lc);
    await shot(a, '2-hauptgeraet-chip-dunkel');
    // §4.3 Echo-Schutz: Mikrofon am Begleitgeraet einschalten, waehrend A Mikrofon/Ton an hat -> Blatt; "Hier einschalten,
    // … stumm" schaltet A per state.ask_mute stumm
    await a2.mouse.move(300, 300);
    await a2.click('.cl-ctl .cl-round[aria-label="Mikrofon einschalten"]');
    const sheet = await until(() => a2.evaluate(() => { const d = document.querySelector('.cl-echo'); return d && d.textContent; }), 4000);
    ok('§4.3: Begleitgeraet - Echo-Blatt "Ist … im selben Raum?" mit "Hier einschalten, … stumm"', sheet && /im selben Raum/.test(sheet) && /Hier einschalten/.test(sheet), sheet);
    await shot(a2, '2b-echo-blatt-dunkel');
    if (sheet) await a2.click('.cl-echo-go');
    const em = await until(async () => { const x = await room(a), y = await room(a2); return x && y && !x.mic && !x.spkOut && y.mic && [x.mic, y.mic]; }, 6000);
    ok('§4.3: A ist stumm (Mikrofon und Ton aus), A2 hat das Mikrofon', em, [(await room(a)), (await room(a2))].map(r => r && { mic: r.mic, spk: r.spkOut }));
    const toastA = await a.evaluate(() => document.body.innerText.includes('übernommen hat'));
    ok('§4.3: A zeigt "Stumm, weil … übernommen hat"', toastA);
    // zurueck: A2 stumm, A wieder an (A ist Hauptgeraet: kein Blatt)
    await a2.click('.cl-ctl .cl-round[aria-label="Stummschalten"]');
    await a.mouse.move(300, 300);
    await a.click('.cl-ctl .cl-round[aria-label="Mikrofon einschalten"]');
    await a.click('.cl-ctl .cl-spk');
    ok('... A wieder mit Mikrofon und Ton (Hauptgeraet ohne Blatt)', await until(async () => { const x = await room(a); return x.mic && x.spkOut; }, 4000) && !(await a.$('.cl-echo')));

    // --- S3: Begleitgeraet teilt den Bildschirm -------------------------------------------------------------------
    await a2.click('.cl-ctl .cl-round[aria-label="Bildschirm teilen"]');
    const rb3 = await until(async () => { const r = await room(b); return r && r.mesh && r.meet && r.meet.layout === 'presentation' && r.meet.stage && /:scr$/.test(r.meet.stage)
      && r.meet.els.some(x => x.id === r.meet.stage && x.video) && r; }, 20000);
    ok('S3: Ben - Auto = Praesentation, Buehne = Bildschirmkachel mit Bild', rb3, await room(b));
    const m3 = rb3 && rb3.meet;
    const stEl = m3 && m3.els.find(x => x.id === m3.stage);
    ok(`S3: Buehne "${stEl && stEl.label}" (Bildschirm von Anna mit Geraet)`, stEl && /^Bildschirm von .+ \(.+\)/.test(stEl.label), stEl);
    const camTile = m3 && m3.els.find(x => x.kind === 'person' && !x.me);
    ok('S3: Annas Kamera als eigene Kachel im Streifen (mit Bild), eigene Kachel zuletzt im Streifen', camTile && m3.strip[0] === camTile.id && camTile.video
       && m3.strip[m3.strip.length - 1] === m3.els.find(x => x.me).id && !m3.float, m3);
    ok('S3: genau zwei Kacheln anderer (Anna + Bildschirm), A2 ohne eigene Personenkachel', rb3 && rb3.tiles === 2, rb3 && rb3.meet.els);
    const l3 = await until(async () => { const x = await look(b); return x.rect && x; }, 8000) || await look(b);
    ok('S3 (§5): Bildschirm contain, schwarze Buehne, nur eine Videospur in der Buehne, kein Kamera-Bild-im-Bild', l3.fit === 'contain' && l3.stageVideos === 1 && !l3.pipInScreen
       && /rgb\(0, 0, 0\)/.test(l3.stageBg), l3);
    ok('S3 (§5.5): kein Banner oben, Hinweis-Chip "… teilt den Bildschirm" unten links', !l3.banner && /teilt den Bildschirm/.test(l3.chipText), l3);
    ok('S3: Layout-Knopf sichtbar ("Layout, Auto")', l3.layoutBtn && l3.layoutLabel === 'Layout, Auto', l3.layoutLabel);
    ok('S3 (§7): eigenes Bild schwebt nicht ueber dem Bildschirm', !(await selfBox(b)));
    const sc3 = await scr(b);
    ok('HKCalls.screen(): gross = Bildschirm, klein = Kamera, Leiste = Chip', sc3 && sc3.big === 'screen' && sc3.small === 'cam' && /teilt den Bildschirm/.test(sc3.bar), sc3);
    await shot(b, '3-praesentation-leisten-dunkel');
    // §9 Teilnehmerliste im Mesh: ab 900 px Seitenleiste, je Person eine Zeile, Anna mit beiden Geraeten
    await b.click('.cl-ctl .cl-people');
    const pl = await until(() => b.evaluate(() => { const p = document.querySelector('.cl-mlist'); const w = document.querySelector('.cl-win');
      return p && { rows: [...p.querySelectorAll('.cl-pl')].map(x => x.textContent), side: w.classList.contains('plist-side'), meetW: document.querySelector('.cl-meet').clientWidth }; }), 4000);
    ok('§9: Teilnehmerliste als Seitenleiste (Buehne schmaler), zwei Personen, Anna mit zwei Geraeten', pl && pl.side && pl.rows.length === 2 && pl.meetW <= 1280 - 320
       && pl.rows.some(r => /Begleitgerät/.test(r)), pl);
    await shot(b, '3b-teilnehmerliste-dunkel');
    await b.click('.cl-ctl .cl-people');
    await until(() => b.evaluate(() => !document.querySelector('.cl-mlist')), 3000);
    await sleep(400);

    // --- A1: Leisten blenden nach 4 s aus; Flaeche >= 70 % --------------------------------------------------------------
    await b.mouse.move(640, 360); const t0 = Date.now();
    const idle = await until(async () => { const x = await look(b); return x.idle && x.top === 0 && x.ctl === 0 && x; }, 7000, 100);
    PERF.ausblenden_ms = Date.now() - t0;
    ok(`A1: nach ${(PERF.ausblenden_ms / 1000).toFixed(1)} s ohne Bedienung: Kopf, Knoepfe, Chip, Namensleiste der Buehne weg`, idle && idle.chip === 0 && idle.stageName === 0
       && PERF.ausblenden_ms >= 3500 && PERF.ausblenden_ms <= 5500, idle || await look(b));
    await sleep(300);
    const lA = await look(b);
    PERF.flaeche_bildschirm = lA.area;
    ok(`S3: geteilter Inhalt nutzt ${Math.round(lA.area * 100)} % der Fensterflaeche (1280x720, Leisten aus; Ziel >= 70 %)`, lA.area >= 0.70, lA);
    await shot(b, '4-praesentation-ausgeblendet-dunkel');
    await b.emulateMedia({ colorScheme: 'light' }); await sleep(400);
    await shot(b, '4-praesentation-ausgeblendet-hell');
    await b.mouse.move(700, 300); await b.mouse.move(720, 320);
    const back = await until(async () => { const x = await look(b); return !x.idle && x.top === 1 && x.ctl === 1 && x; }, 2000, 50);
    ok('A1: Mausbewegung holt die Leisten zurueck', back, await look(b));
    await shot(b, '5-praesentation-leisten-hell');
    await b.emulateMedia({ colorScheme: 'dark' });

    // --- Lieferung 2: Layout-Menue, Anheften, Zoom, Kuerzel ---------------------------------------------------------
    await b.click('.cl-ctl .cl-layout');
    const menu = await b.evaluate(() => [...document.querySelectorAll('.cl-lmenu [role=menuitemradio]')].map(x => [x.textContent, x.getAttribute('aria-checked'), x.disabled]));
    ok('Layout-Menue: Auto (gewaehlt), Galerie, Sprecher, Praesentation (aktiv, weil jemand teilt)', menu.length === 4 && menu[0][1] === 'true' && !menu[3][2], menu);
    await shot(b, '6-layout-menue-dunkel');
    await b.click('.cl-lmenu [role=menuitemradio]:nth-child(2)');
    const gal = await until(async () => { const r = await room(b); return r.meet && r.meet.layout === 'gallery' && r.meet.grid.length === 3 && r; }, 4000);
    const stored = await b.evaluate(() => localStorage.getItem('saga-call-layout'));
    ok('Galerie per Hand: drei Kacheln im Raster (Bildschirm, Anna, Du), gespeichert in saga-call-layout', gal && stored === 'gallery', [gal && gal.meet, stored]);
    await shot(b, '7-galerie-dunkel');
    await b.keyboard.press('a');
    ok('Kuerzel A: zurueck auf Auto (Praesentation)', await until(async () => { const r = await room(b); return r.meet && r.meet.layout === 'presentation' && r.meet.choice === 'auto'; }, 4000));
    const camId = camTile.id;
    await b.click(`.cl-mstrip .cl-tile[data-id="${camId}"]`);
    const pinned = await until(async () => { const r = await room(b); return r.meet && r.meet.pin === camId && r.meet.stage === camId && r; }, 4000);
    const scP = await scr(b);
    ok('Anheften: Antippen der Kamera im Streifen holt sie auf die Buehne, der Bildschirm rueckt vorn in den Streifen', pinned && pinned.meet.strip[0] === m3.stage && scP.big === 'cam', [pinned && pinned.meet, scP]);
    const pinLab = await b.evaluate(id => document.querySelector(`.cl-tile[data-id="${id}"]`).getAttribute('aria-label'), camId);
    ok('Anheften: Vorlesetext "… angeheftet"', /angeheftet/.test(pinLab), pinLab);
    await b.keyboard.press('Escape');
    ok('Esc loest den Pin (Bildschirm wieder auf der Buehne)', await until(async () => { const r = await room(b); return r.meet && !r.meet.pin && r.meet.stage === m3.stage; }, 4000));
    await b.mouse.move(400, 300);
    await b.keyboard.press('Control+Equal');
    const z = await b.evaluate(() => { const v = document.querySelector('.cl-mbig .cl-tile video'); const c = document.querySelector('.cl-zchip'); return { tf: v && v.style.transform, chip: c && !c.hidden && c.textContent }; });
    ok('Zoom: Strg + vergroessert den geteilten Bildschirm (Chip "125 %")', /scale\(1\.25\)/.test(z.tf) && /125 %/.test(z.chip), z);
    await b.keyboard.press('Control+Digit0');
    ok('Zoom: Strg 0 setzt zurueck', await b.evaluate(() => !document.querySelector('.cl-mbig .cl-tile video').style.transform));
    const mic0 = (await room(b)).mic;
    await b.keyboard.press('m');
    const mic1 = await until(async () => { const r = await room(b); return r.mic !== mic0 && r; }, 2000);
    await b.keyboard.press('m');
    ok('Kuerzel M schaltet das Mikrofon (aus und wieder an)', mic1 && await until(async () => (await room(b)).mic === mic0, 2000));
    await b.keyboard.press('Shift+Slash');
    const keys = await b.evaluate(() => { const d = document.querySelector('.cl-keys'); return d && d.textContent; });
    ok('Kuerzel ?: Liste der Tastenkuerzel', keys && /Vollbild/.test(keys) && /Galerie/.test(keys), keys);
    await shot(b, '8-kuerzel-dunkel');
    await b.keyboard.press('Escape');
    const mTitle = await b.evaluate(() => (document.querySelector('.cl-ctl .cl-round') || {}).title);
    ok('Tooltips mit Kuerzel ("… (M)")', /\(M\)$/.test(mTitle || ''), mTitle);

    // --- Handy-Breite (A3) ---------------------------------------------------------------------------------------------
    await b.setViewportSize({ width: 390, height: 844 }); await sleep(900);
    const lp = await look(b), rp = await room(b);
    ok('A3: Handy hochkant - kein waagrechtes Scrollen, Streifen unten', !lp.scrollX && rp.meet && rp.meet.stripSide === 'bottom' && rp.meet.layout === 'presentation', [lp, rp.meet]);
    const cf = await b.evaluate(CTLFIT);
    ok(`Handy 390 px: Bedienleiste in einer Zeile im Fenster, Auflegen ganz sichtbar, Nebenknoepfe unter "Mehr" (${cf.n} Knoepfe)`, cf.inside && cf.oneRow && cf.red && cf.more && cf.n <= 5, cf);
    await shot(b, '9-handy-dunkel');
    await b.click('.cl-ctl .cl-more');
    const mm = await b.evaluate(() => [...document.querySelectorAll('.cl-moremenu .cl-more-i')].map(x => x.getAttribute('aria-label')));
    ok('Handy: Menue "Mehr" mit Ton, Layout und Teilnehmenden', mm.some(x => /^Ton/.test(x)) && mm.some(x => /^Layout/.test(x)) && mm.some(x => /Teilnehmende/.test(x)), mm);
    await shot(b, '9-handy-mehr-dunkel');
    await b.click('.cl-moremenu .cl-layout');
    ok('Handy: Layout aus "Mehr" oeffnet das Layout-Menue', await until(() => b.evaluate(() => !!document.querySelector('.cl-lmenu')), 2000));
    await shot(b, '9-handy-layout-dunkel');
    await b.keyboard.press('Escape');
    await b.emulateMedia({ colorScheme: 'light' }); await sleep(300);
    await shot(b, '9-handy-hell');
    await b.emulateMedia({ colorScheme: 'dark' });
    await b.setViewportSize({ width: 1280, height: 720 }); await sleep(600);

    // --- Teilen endet -> Einzelansicht -------------------------------------------------------------------------------
    await a2.click('.cl-share-stop');
    const end = await until(async () => { const r = await room(b); return r && !r.mesh && r; }, 12000);
    ok('Teilen beendet: Ben wieder in der Einzelansicht (eine Person)', end, await room(b));
    const sbE = await until(async () => selfBox(b), 4000);
    ok('... eigenes Bild wieder schwebend', !!sbE, sbE);
    await b.click('.cl-ctl .cl-red');
    ok('Auflegen beendet den Anruf auf allen Geraeten', await until(async () => !(await room(a)) && !(await room(a2)), 12000));
    ok('A4: Layout-Wahl bleibt nach dem Auflegen gespeichert (Auto)', (await b.evaluate(() => localStorage.getItem('saga-call-layout'))) === 'auto');
  } catch (e) {
    ok('Ablauf', false, String(e && e.stack || e));
  } finally {
    for (const br of BROWSERS) await br.close().catch(() => {});
    console.log('PERF ' + JSON.stringify(PERF));
    const n = res.filter(Boolean).length;
    console.log(`\nBROWSER-MEETING ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
    process.exit(n === res.length ? 0 : 1);
  }
})();
