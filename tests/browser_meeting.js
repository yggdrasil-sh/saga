// Browser-Ende-zu-Ende Meeting-Ansicht (MEETING-SPEC.md, Lieferung 1/2, static/meeting.js + static/calls.js) - drei
// Playwright-Kontexte (je ein eigener Chromium) mit Kamera/Mikrofon-Attrappe: Anna mit zwei Geraeten (A = Hauptgeraet,
// A2 = Begleitgeraet), Ben (B, 1280x720). Laeuft gegen einen Test-Stack wie tests/browser_raum.js (Mesh, ohne Knoten):
//   SAGA_BASE=http://host:port SAGA_USER=anna SAGA_PASS=... SAGA_USER2=ben SAGA_PASS2=... [SHOTS=/pfad] [SHOT_PREFIX=meet-web-] \
//   [PLAYWRIGHT=...] [PW_CHANNEL=chromium] node tests/browser_meeting.js
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
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
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
const look = p => p.evaluate(LOOK);
const selfBox = p => p.evaluate(() => { const v = document.querySelector('.cl-local'); if (!v || v.hidden || getComputedStyle(v).display === 'none') return null;
  const r = v.getBoundingClientRect(); return { w: r.width, h: r.height, vw: v.videoWidth, vh: v.videoHeight }; });

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
      console.log('SKIP browser_meeting: calls.horn_all ist an (Anrufe ueber Knoten) - dieser Test prueft den Mesh-Weg.'); process.exit(0);
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
    await shot(b, '9-handy-dunkel');
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
