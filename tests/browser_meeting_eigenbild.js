// Browser-Ende-zu-Ende (Bugs-DB 2161): das eigene Bild richtet sich nach der Ausrichtung der KAMERA, nicht nach der Buehne
// (wie iOS/Android, gemeinsame Vektoren tests/meeting_vectors.json selfPipSize) - plus Bilder der Meeting-Ansicht, die
// iOS/Android an das Web angleichen (Galerie mit zwei Kacheln, Bildschirm in der Galerie vorn). Zwei Playwright-Kontexte,
// Kamera-Attrappe mit fester Groesse (Canvas statt Fake-Geraet, damit hochkant/quer sicher ist). Mesh, ohne Knoten:
//   SAGA_BASE=http://host:port SAGA_USER=anna SAGA_PASS=... SAGA_USER2=ben SAGA_PASS2=... [SHOTS=/pfad] [SHOT_PREFIX=eigenbild-] \
//   [PLAYWRIGHT=...] [PW_CHANNEL=chromium | CHROME=/usr/bin/chromium] node tests/browser_meeting_eigenbild.js
// Geprueft:
// - Desktop 1280x720, Kamera hochkant 360x640: eigenes Bild hochkant, Breitenregel hochkant (hoechstens 160 px breit),
//   Groesse = SagaMeeting.selfPipSize der Seite (+-2 px)
// - Galerie gewaehlt (naechster Anruf), 1:1 ohne Teilen: Galerie mit zwei Kacheln (nicht 1:1); Anna teilt: Bildschirm vorn
// - Handy 390x844 (Touch), Kamera quer 640x360: eigenes Bild quer, Breitenregel quer (mindestens 120 px breit)
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_USER2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '', PFX = E.SHOT_PREFIX || 'eigenbild-';
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, PFX + name + '.png') }); } }
async function until(fn, ms = 20000, step = 150) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const sleep = ms => new Promise(r => setTimeout(r, ms));

// Kamera-Attrappe: getUserMedia liefert statt des Fake-Geraets ein Canvas-Bild cam.w x cam.h (Ton bleibt vom Fake-Geraet)
const CAM_HOOK = cam => {
  try { localStorage.setItem('saga-screen-hint', '0'); } catch (e) { /* egal */ }
  const md = navigator.mediaDevices; if (!md) return;
  const orig = md.getUserMedia.bind(md);
  md.getUserMedia = async c => {
    const s = await orig(c);
    if (!cam || !c || !c.video) return s;
    s.getVideoTracks().forEach(t => { s.removeTrack(t); t.stop(); });
    const cv = document.createElement('canvas'); cv.width = cam.w; cv.height = cam.h;
    window.__camSet = (w, h) => { cam.w = w; cam.h = h; cv.width = w; cv.height = h; };   // Kamera drehen mitten im Anruf
    const g = cv.getContext('2d'); let n = 0;
    setInterval(() => {
      g.fillStyle = '#3a6b8c'; g.fillRect(0, 0, cv.width, cv.height);
      g.fillStyle = '#e9d8a6'; g.beginPath(); g.arc(cv.width / 2, cv.height * 0.42, Math.min(cv.width, cv.height) * 0.18, 0, 7); g.fill();
      g.fillRect(cv.width * 0.3, cv.height * 0.62, cv.width * 0.4, cv.height * 0.38);
      g.fillStyle = '#fff'; g.font = Math.round(Math.min(cv.width, cv.height) / 9) + 'px sans-serif';
      g.fillText((cam.w > cam.h ? 'Kamera quer ' : 'Kamera hochkant ') + (n++ % 10), 12, Math.round(Math.min(cv.width, cv.height) / 7));
    }, 100);
    s.addTrack(cv.captureStream(15).getVideoTracks()[0]);
    return s;
  };
  // Bildschirm-Attrappe (Farbbalken 1280x720) statt der Auswahl des Browsers
  md.getDisplayMedia = async () => {
    const c = document.createElement('canvas'); c.width = 1280; c.height = 720;
    const g = c.getContext('2d'), cols = ['#e6e6e6', '#e6e600', '#00e6e6', '#00e600', '#e600e6', '#e60000', '#0000e6'];
    setInterval(() => { cols.forEach((x, i) => { g.fillStyle = x; g.fillRect(i * c.width / 7, 0, c.width / 7 + 1, c.height); }); }, 100);
    return c.captureStream(15);
  };
};
const BROWSERS = [];
async function session(user, pass, name, o) {
  const origin = new URL(BASE).origin;
  const br = await chromium.launch({ ...(E.CHROME ? { executablePath: E.CHROME } : { channel: E.PW_CHANNEL || 'chrome' }), headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin, '--autoplay-policy=no-user-gesture-required'] });
  BROWSERS.push(br);
  const ctx = await br.newContext({ viewport: o.vp, locale: 'de-DE', colorScheme: 'dark', hasTouch: !!o.touch, isMobile: !!o.touch });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.on('pageerror', e => console.log('  [pageerror ' + name + '] ' + e.message));
  await p.addInitScript(CAM_HOOK, o.cam || null);
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', pass);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  p.__br = br;
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
// eigenes Bild: Rahmen, Kamera-Groesse und die Groesse, die SagaMeeting.selfPipSize fuer diese Buehne liefert
const selfBox = p => p.evaluate(() => {
  const v = document.querySelector('.cl-local'), st = document.querySelector('.cl-win .cl-stage');
  if (!v || v.hidden || getComputedStyle(v).display === 'none') return null;
  const r = v.getBoundingClientRect(), vv = v.querySelector('video') || v;
  let size = 'M'; try { size = (JSON.parse(localStorage.getItem('saga-call-self') || '{}') || {}).size || 'M'; } catch (e) { /* egal */ }
  const want = st && vv.videoWidth && window.SagaMeeting ? window.SagaMeeting.selfPipSize(st.clientWidth, st.clientHeight, vv.videoWidth / vv.videoHeight, size) : null;
  return { w: Math.round(r.width), h: Math.round(r.height), vw: vv.videoWidth, vh: vv.videoHeight, stage: st ? [st.clientWidth, st.clientHeight] : null, size, want };
});
const fits = s => s && s.want && Math.abs(s.w - s.want.w) <= 2 && Math.abs(s.h - s.want.h) <= 2;
// Rahmengroessen je Bild mitschreiben (Flackern/Springen beim Drehen: nur alte und neue Groesse erlaubt)
const sizesStart = p => p.evaluate(() => { window.__sz = []; window.__szOn = true; const f = () => { const v = document.querySelector('.cl-local'); if (v) { const r = v.getBoundingClientRect(); const k = Math.round(r.width) + 'x' + Math.round(r.height); if (window.__sz[window.__sz.length - 1] !== k) window.__sz.push(k); } if (window.__szOn) requestAnimationFrame(f); }; f(); });
const sizesStop = p => p.evaluate(() => { window.__szOn = false; return window.__sz; });
const near = (k, s) => { const [w, h] = k.split('x').map(Number); return Math.abs(w - s.w) <= 2 && Math.abs(h - s.h) <= 2; };
async function call(a, b, U2) {
  await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1000);
  await b.goto(BASE + '/chats'); await liveOk(a, b);
  await a.click('#ct-call-v');
  await b.waitForSelector('.cl-ring', { timeout: 20000 }); await b.click('.cl-ring .cl-big');
  return until(async () => { const r = await room(b); return r && r.links.length === 1 && r.links[0].connected && r.links[0].verified && r; }, 30000);
}
async function hangup(a, b) {
  await b.mouse.move(100, 300); await b.click('.cl-ctl .cl-red').catch(() => {});
  return until(async () => !(await room(a)) && !(await room(b)), 15000);
}

(async () => {
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  try {
    const a = await session(U1, E.SAGA_PASS, 'A', { vp: { width: 1280, height: 800 } });
    const b = await session(U2, E.SAGA_PASS2, 'B', { vp: { width: 1280, height: 720 }, cam: { w: 360, h: 640 } });
    await chatKeys(a, U1); await chatKeys(b, U2);
    await b.evaluate(() => { try { localStorage.removeItem('saga-call-layout'); localStorage.removeItem('saga-call-self'); } catch (e) { /* egal */ } });

    // --- Desktop, Kamera hochkant ------------------------------------------------------------------------------------
    ok('1:1 A <-> B verbunden (B: Desktop 1280x720, Kamera hochkant)', await call(a, b, U2), await room(b));
    const s1 = await until(async () => { const s = await selfBox(b); return s && s.vw && s.w > 0 && fits(s) && s; }, 10000) || await selfBox(b);   // Pruefer 2161: auf das Anpassen warten (sonst Wettlauf mit 'resize')
    ok(`Desktop, Kamera hochkant (${s1 && s1.vw}x${s1 && s1.vh}): eigenes Bild hochkant (${s1 && s1.w}x${s1 && s1.h})`, s1 && s1.vh > s1.vw && s1.h > s1.w, s1);
    ok('... Breitenregel nach der Kamera (hochkant: hoechstens 160 px breit, nicht 22 % der queren Buehne)', s1 && s1.w <= 161, s1);
    ok('... Rahmen = SagaMeeting.selfPipSize (+-2 px)', s1 && s1.want && Math.abs(s1.w - s1.want.w) <= 2 && Math.abs(s1.h - s1.want.h) <= 2, s1);
    await b.mouse.move(640, 360); await sleep(300);
    await shot(b, 'desktop-kamera-hochkant');

    // --- Pruefer 2161: Kamera mitten im Anruf drehen (hochkant -> quer -> hochkant), Groesse folgt ohne Zwischenstufen --
    for (const [cw, ch, quer] of [[640, 360, true], [360, 640, false]]) {
      const vor = await selfBox(b);
      await sizesStart(b);
      await b.evaluate(([w, h]) => window.__camSet(w, h), [cw, ch]);
      const s = await until(async () => { const x = await selfBox(b); return x && (quer ? x.vw > x.vh : x.vh > x.vw) && fits(x) && x; }, 10000);
      await sleep(600);
      const seen = await sizesStop(b);
      ok(`Kamera gedreht auf ${cw}x${ch}: eigenes Bild folgt (${s && s.w}x${s && s.h}, ${quer ? 'quer' : 'hochkant'}) = selfPipSize`, s && (quer ? s.w > s.h : s.h > s.w), s || await selfBox(b));
      ok('... ohne Zwischengroessen (nur alt -> neu)', s && seen.length <= 2 && seen.every(k => near(k, vor) || near(k, s)), { vor, seen, s });
    }
    // --- Pruefer 2161: S/M/L per Doppelklick (L -> S -> M), jeweils = selfPipSize mit der Kamera hochkant ----------------
    for (const sz of ['L', 'S', 'M']) {
      await b.dblclick('.cl-local'); await sleep(400);
      const s = await until(async () => { const x = await selfBox(b); return x && x.size === sz && fits(x) && x; }, 4000);
      ok(`Groesse ${sz} per Doppelklick: Rahmen = selfPipSize (${s && s.w}x${s && s.h})`, s && !(await b.evaluate(() => document.querySelector('.cl-win').classList.contains('self-min'))), s || await selfBox(b));
    }
    // --- Pruefer 2161: sehr schmales und sehr breites Fenster -----------------------------------------------------------
    for (const vp of [{ width: 360, height: 720 }, { width: 2400, height: 600 }, { width: 1280, height: 720 }]) {
      await b.setViewportSize(vp); await sleep(500);
      const s = await until(async () => { const x = await selfBox(b); return fits(x) && x; }, 4000);
      ok(`Fenster ${vp.width}x${vp.height}, Kamera hochkant: Rahmen = selfPipSize (${s && s.w}x${s && s.h}), passt in die Buehne`,
        s && s.h * 2 <= s.stage[1] + 2 && s.w <= s.stage[0], s || await selfBox(b));
    }
    // --- Pruefer 2161: Kamera aus und wieder an - Platzhalter bleibt sichtbar, danach wieder nach der Kamera -------------
    await b.mouse.move(640, 600);
    await b.click('.cl-ctl .cl-round >> nth=1');
    const off = await until(async () => b.evaluate(() => { const v = document.querySelector('.cl-local'); const r = v && v.getBoundingClientRect(); return v && !v.hidden && !v.classList.contains('live') && r.width >= 60 && r.height >= 40 && [Math.round(r.width), Math.round(r.height)]; }), 6000);
    ok('Kamera aus: eigenes Bild bleibt als Platzhalter mit sinnvoller Groesse', off, await selfBox(b));
    await b.click('.cl-ctl .cl-round >> nth=1');
    const on = await until(async () => { const x = await selfBox(b); return x && x.vh > x.vw && fits(x) && x; }, 10000);
    ok(`Kamera wieder an: eigenes Bild wieder hochkant nach der Kamera (${on && on.w}x${on && on.h})`, on, await selfBox(b));
    // --- Pruefer 2161: eigener Bildschirm geteilt - das eigene Bild bleibt die Kamera (§1.5), Groesse nach der Kamera ----
    await b.mouse.move(640, 600);
    await b.click('.cl-ctl .cl-round[aria-label="Bildschirm teilen"]');
    const sh = await until(async () => { const r = await b.evaluate(() => document.querySelector('.cl-win').classList.contains('screen')); const x = await selfBox(b); return r && x && x.vh > x.vw && fits(x) && x; }, 10000);
    ok(`Eigener Bildschirm geteilt: eigenes Bild bleibt Kamera hochkant (${sh && sh.w}x${sh && sh.h}) = selfPipSize`, sh, await selfBox(b));
    await b.click('.cl-share-stop').catch(() => {});
    await sleep(800);

    ok('Auflegen beendet den Anruf', await hangup(a, b));

    // --- Galerie gewaehlt im 1:1 ohne Teilen: Galerie mit zwei Kacheln (Wahl gilt ab dem naechsten Anruf) ---------------
    await b.evaluate(() => localStorage.setItem('saga-call-layout', 'gallery'));
    ok('1:1 A <-> B verbunden (B hat Galerie gewaehlt)', await call(a, b, U2), await room(b));
    const g2 = await until(async () => { const r = await room(b); return r && r.meet && r.meet.layout === 'gallery' && r.meet.grid && r.meet.grid.length === 2 && r; }, 10000);
    ok('Galerie gewaehlt, 1:1 ohne Teilen: Galerie mit zwei Kacheln (nicht 1:1)', g2, (await room(b) || {}).meet);
    await b.mouse.move(640, 360); await sleep(800);
    await shot(b, 'galerie-zwei');
    await a.mouse.move(300, 300);
    await a.click('.cl-ctl .cl-round[aria-label="Bildschirm teilen"]');
    const gs = await until(async () => { const r = await room(b); return r && r.meet && r.meet.layout === 'gallery' && r.meet.grid && r.meet.grid.length === 3 && r; }, 20000);
    ok('Anna teilt, Galerie gewaehlt: Bildschirm vorn in der Galerie, eigene Kachel zuletzt', gs && /:scr$/.test(gs.meet.grid[0]) && gs.meet.els.find(x => x.id === gs.meet.grid[2]).me,
       (await room(b) || {}).meet);
    await b.mouse.move(640, 360); await sleep(1500);
    await shot(b, 'galerie-bildschirm-vorn');
    await a.click('.cl-share-stop').catch(() => {});
    await sleep(800);
    ok('Auflegen beendet den Anruf', await hangup(a, b));
    await b.evaluate(() => { try { localStorage.removeItem('saga-call-layout'); } catch (e) { /* egal */ } });
    await b.__br.close();

    // --- Handy hochkant, Kamera quer (gedreht) --------------------------------------------------------------------------
    const m = await session(U2, E.SAGA_PASS2, 'B-Handy', { vp: { width: 390, height: 844 }, touch: true, cam: { w: 640, h: 360 } });
    await chatKeys(m, U2);
    await m.evaluate(() => { try { localStorage.removeItem('saga-call-layout'); localStorage.removeItem('saga-call-self'); } catch (e) { /* egal */ } });
    ok('1:1 A <-> B verbunden (B: Handy 390x844, Kamera quer)', await call(a, m, U2), await room(m));
    const s2 = await until(async () => { const s = await selfBox(m); return s && s.vw && s.w > 0 && fits(s) && s; }, 10000) || await selfBox(m);   // Pruefer 2161: auf das Anpassen warten (sonst Wettlauf mit 'resize')
    ok(`Handy hochkant, Kamera quer (${s2 && s2.vw}x${s2 && s2.vh}): eigenes Bild quer (${s2 && s2.w}x${s2 && s2.h})`, s2 && s2.vw > s2.vh && s2.w > s2.h, s2);
    ok('... Breitenregel nach der Kamera (quer: mindestens 120 px breit, nicht 26 % der hochkanten Buehne)', s2 && s2.w >= 119, s2);
    ok('... Rahmen = SagaMeeting.selfPipSize (+-2 px)', s2 && s2.want && Math.abs(s2.w - s2.want.w) <= 2 && Math.abs(s2.h - s2.want.h) <= 2, s2);
    await sleep(300);
    await shot(m, 'handy-kamera-quer');
    ok('Auflegen beendet den Anruf', await hangup(a, m));
  } catch (e) {
    ok('Ablauf', false, String(e && e.stack || e));
  } finally {
    for (const br of BROWSERS) await br.close().catch(() => {});
    const n = res.filter(Boolean).length;
    console.log(`\nBROWSER-MEETING-EIGENBILD ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
    process.exit(n === res.length ? 0 : 1);
  }
})();
