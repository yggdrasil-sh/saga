// Browser-Ende-zu-Ende fuer Videos im Chat (2.4, Etappe E2) - zwei Playwright-Kontexte im headless Chromium:
//   SAGA_BASE=http://host:port SAGA_USER=konto-a SAGA_PASS=... SAGA_USER2=konto-b SAGA_PASS2=... VIDEO_BIG=/pfad/gross.webm
//   VIDEO_SMALL=/pfad/klein.webm [SAGA_LOG=/pfad/server.log] [SHOTS=/pfad] [PLAYWRIGHT=...] node tests/browser_videos.js
// Der Server sollte mit kleiner ATTACH_MAX_MB laufen (z. B. 2), damit VIDEO_BIG im Format saga-file/v2 geht.
// Geprueft: A haengt ein grosses Video an (Poster, Dauer, Abmessungen aus dem Browser), Upload in Teilen (saga-file/v2) ->
// Nachricht; B sieht nur Poster und Dauer (kein <video> in der Liste), Abspielen ueber den Service Worker (/_m/...,
// Range, blockweise entschluesselt), Spulen ans Ende, Bytes vom Server nur bereichsweise (kein Komplett-Download),
// Herunterladen als entschluesselte Datei (Download-Strom ueber den Service Worker, gleiche Bytes wie das Original);
// kleines Video (bis ATTACH_MAX_MB, v1) wie bisher; "optimiert 720p" nur angeboten, wenn der Browser MP4 aufnehmen kann.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os'), crypto = require('crypto');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2 || !E.VIDEO_BIG || !E.VIDEO_SMALL) {
  console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2, VIDEO_BIG, VIDEO_SMALL setzen'); process.exit(2);
}
const SHOTS = E.SHOTS || '';
const res = [], PERF = {};
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, 'video-' + name + '.png') }); } }
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
async function until(fn, ms = 20000, step = 150) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

async function session(browser, user, pass) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE', colorScheme: 'dark', acceptDownloads: true });
  const p = await ctx.newPage();
  p.on('pageerror', e => console.log('  [pageerror ' + user + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + user + '] ' + m.text()));
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
// Bytes, die saga-media fuer eine Datei ausgeliefert hat (Zugriffszeilen "[saga-media N B ...]")
function servedBytes(fid) {
  if (!E.SAGA_LOG || !fid) return null;
  let n = 0;
  for (const l of fs.readFileSync(E.SAGA_LOG, 'utf8').split('\n')) {
    if (l.indexOf('/api/chat/files/' + fid + ' ') < 0) continue;
    const m = /\[saga-media (\d+) B/.exec(l); if (m) n += +m[1];
  }
  return n;
}

(async () => {
  const origin = new URL(BASE).origin;
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true,
    args: ['--unsafely-treat-insecure-origin-as-secure=' + origin, '--autoplay-policy=no-user-gesture-required'] });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  const big = fs.readFileSync(E.VIDEO_BIG), small = fs.readFileSync(E.VIDEO_SMALL);
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2);
    await chatKeys(a, U1); await chatKeys(b, U2);
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
    const lim = await a.evaluate(() => fetch('/api/chat/me', { headers: { 'X-Saga': '1' } }).then(r => r.json()).then(d => d.limits));
    ok('limits: file2, video_max, upload_part', lim.file2 === true && lim.video_max >= big.length && lim.upload_part === 8 << 20, lim);
    const opt = await a.evaluate(() => window.SagaChatVideo.canOptimize());
    console.log('     optimiert 720p moeglich: ' + (opt || 'nein (kein MP4-Aufnehmen in diesem Browser)'));

    // --- 1. grosses Video senden (ein Teil bricht unterwegs ab -> HEAD, fortsetzen) -----------------------------------
    let puts = 0, aborted = 0, heads = 0;
    await a.route('**/api/chat/uploads/*', async (route) => {
      const m = route.request().method();
      if (m === 'HEAD') heads++;
      if (m === 'PUT' && ++puts === 2 && !aborted) { aborted++; return route.abort('connectionreset'); }
      return route.continue();
    });
    let t0 = Date.now();
    await a.setInputFiles('#ct-file', E.VIDEO_BIG);
    const chip = await a.textContent('#ct-files');
    ok('Anhang-Chip als Video (🎬)', chip.indexOf('🎬') >= 0, chip);
    if (opt) {
      ok('Schalter "optimiert 720p" vorhanden (Desktop: aus)', await a.$eval('.ct-chip-opt input', i => !i.checked).catch(() => false));
      await a.uncheck('.ct-chip-opt input').catch(() => {});
    }
    await a.fill('#ct-input', 'Mein Film');
    await a.click('#ct-send');
    const sentFid = await until(() => a.evaluate(() => {
      const m = [...document.querySelectorAll('#ct-msgs .ct-vid')]; return m.length ? true : false;
    }), 120000);
    PERF.senden_gross_ms = Date.now() - t0;
    ok(`A: grosses Video gesendet (${PERF.senden_gross_ms} ms fuer ${(big.length / 1048576).toFixed(1)} MB)`, !!sentFid);
    ok(`A: Upload nach Abbruch fortgesetzt (${puts} PUT, ${heads} HEAD)`, aborted === 1 && heads >= 1 && puts >= 5, { puts, heads });
    await a.unroute('**/api/chat/uploads/*');
    await shot(a, 'a-gesendet');

    // --- 2. B: Poster, Dauer, kein <video> ------------------------------------------------------------------------------
    await b.goto(BASE + '/chats#u' + encodeURIComponent(U1)); await b.waitForSelector('#ct-chat:not([hidden])');
    const poster = await until(() => b.evaluate(() => {
      const v = document.querySelector('#ct-msgs .ct-vid'); if (!v) return null;
      const im = v.querySelector('img'); const d = v.querySelector('.ct-vid-dur');
      return im && im.complete && im.naturalWidth > 0 ? { w: im.naturalWidth, h: im.naturalHeight, dur: d && d.textContent, box: v.style.aspectRatio } : null;
    }), 30000);
    ok('B: Poster (Canvas-JPEG, <= 320 px) und Dauer 0:20 im Verlauf', poster && Math.max(poster.w, poster.h) <= 320 && poster.dur === '0:20', poster);
    ok('B: Abmessungen aus dem Video (16:9-Kasten)', poster && (() => { const [w, h] = poster.box.split('/').map(Number); return Math.abs(w / h - 16 / 9) < 0.03; })(), poster);
    ok('B: kein <video>-Element in der Chatliste', await b.evaluate(() => document.querySelectorAll('#ct-msgs video').length === 0));
    await shot(b, 'b-liste');

    // --- 3. Abspielen per Service Worker, Spulen ------------------------------------------------------------------------
    t0 = Date.now();
    await b.click('#ct-msgs .ct-vid');
    const playing = await until(() => b.evaluate(() => {
      const v = document.querySelector('.ct-video-player'); return v && v.readyState >= 3 && v.currentTime > 0.3 ? { src: v.getAttribute('src'), t: v.currentTime, d: v.duration } : null;
    }), 30000);
    PERF.start_erstes_bild_ms = Date.now() - t0;
    const vfid0 = await b.evaluate(() => { const x = [...document.querySelectorAll('#ct-msgs .ct-vid')]; return x.length ? x[0].dataset.fid : null; });
    const atStart = servedBytes(vfid0);
    if (atStart === 0) console.log('     (ohne saga-media: Bytes-Pruefung am Zugriffsprotokoll entfaellt)');
    if (atStart) {
      PERF.ausgeliefert_bei_start_mb = +(atStart / 1048576).toFixed(2);
      ok(`Server: Wiedergabe startet, bevor alles geladen ist (${PERF.ausgeliefert_bei_start_mb} MB von ${(big.length / 1048576).toFixed(1)} MB)`, atStart < big.length, atStart);
      const lines = fs.readFileSync(E.SAGA_LOG, 'utf8').split('\n').filter(l => l.indexOf('/api/chat/files/' + vfid0 + ' ') >= 0 && l.indexOf('[saga-media') >= 0);
      ok('Server: der Service Worker holt nur Bereiche (206, je hoechstens 1 MiB + Tags)', lines.length > 3 && lines.every(l => / 206 /.test(l)
        && +(/\[saga-media (\d+) B/.exec(l) || [0, 1e12])[1] <= 4 * (262144 + 16)), lines.slice(0, 3));
    }
    ok(`B: spielt ueber den Service Worker (/_m/..., ${PERF.start_erstes_bild_ms} ms bis zur Wiedergabe)`, playing && /^\/_m\/[0-9a-f]{32}$/.test(playing.src), playing);
    ok('B: Dauer im Player stimmt (20 s)', playing && Math.abs(playing.d - 20) < 0.6, playing);
    await shot(b, 'b-player');
    t0 = Date.now();
    await b.evaluate(() => { const v = document.querySelector('.ct-video-player'); v.currentTime = 17; });
    const seeked = await until(() => b.evaluate(() => { const v = document.querySelector('.ct-video-player'); return v.currentTime > 17.3 && v.readyState >= 3 ? v.currentTime : null; }), 20000);
    PERF.spulen_ans_ende_ms = Date.now() - t0;
    ok(`B: Spulen ans Ende (17 s) spielt weiter (${PERF.spulen_ans_ende_ms} ms)`, !!seeked, seeked);
    await b.evaluate(() => { const v = document.querySelector('.ct-video-player'); v.currentTime = 2; });
    const back = await until(() => b.evaluate(() => { const v = document.querySelector('.ct-video-player'); return v.currentTime > 2.2 && v.currentTime < 8 && v.readyState >= 3 ? v.currentTime : null; }), 20000);
    ok('B: zurueckspulen (2 s) spielt weiter', !!back, back);
    // Server-Seite: wie viel hat saga-media fuer das Video ausgeliefert? (nur Bereiche: nicht die ganze Datei)
    const vfid = await a.evaluate(() => {
      const x = [...document.querySelectorAll('#ct-msgs .ct-vid')]; return x.length ? x[x.length - 1].dataset.fid || null : null;
    });
    const served = servedBytes(vfid);
    if (served !== null) PERF.ausgeliefert_bis_spulen_mb = +(served / 1048576).toFixed(2);   // Vorpuffer bestimmt der Browser
    // --- 4. Herunterladen als entschluesselte Datei (Strom ueber den Service Worker) -----------------------------------
    await b.evaluate(() => { delete window.showSaveFilePicker; window.showSaveFilePicker = undefined; });
    t0 = Date.now();
    const [dl] = await Promise.all([b.waitForEvent('download', { timeout: 60000 }), b.click('.ct-view-save')]);
    const out = path.join(os.tmpdir(), 'saga-video-dl-' + Date.now());
    await dl.saveAs(out);
    const got = fs.readFileSync(out); fs.unlinkSync(out);
    PERF.herunterladen_ms = Date.now() - t0;
    ok(`B: Herunterladen = Original (gleiche Bytes, ${PERF.herunterladen_ms} ms)`, got.length === big.length && sha(got) === sha(big), { len: got.length, want: big.length });
    ok('B: Dateiname aus der Nachricht', dl.suggestedFilename() === path.basename(E.VIDEO_BIG), dl.suggestedFilename());
    await b.keyboard.press('Escape');
    ok('B: Player geschlossen, kein <video> mehr', await until(() => b.evaluate(() => document.querySelectorAll('video').length === 0), 5000));

    // --- 5. kleines Video (v1, wie bisher) ------------------------------------------------------------------------------
    await a.setInputFiles('#ct-file', E.VIDEO_SMALL);
    await a.fill('#ct-input', 'Kurz');
    await a.click('#ct-send');
    const two = await until(() => b.evaluate(() => document.querySelectorAll('#ct-msgs .ct-vid').length >= 2), 40000);
    ok('B: kleines Video angekommen (Poster)', !!two);
    await b.locator('#ct-msgs .ct-vid').last().click();
    const p2 = await until(() => b.evaluate(() => { const v = document.querySelector('.ct-video-player'); return v && v.readyState >= 3 && v.currentTime > 0.2 ? v.getAttribute('src') : null; }), 30000);
    ok('B: kleines Video (v1) spielt als Blob', p2 && p2.indexOf('blob:') === 0, p2);
    await b.keyboard.press('Escape');

    // --- 6. Abmelden leert die Schluessel im Service Worker ------------------------------------------------------------
    const cleared = await b.evaluate(() => new Promise(res => {
      const c = navigator.serviceWorker.controller; if (!c) return res(false);
      const ch = new MessageChannel(); ch.port1.onmessage = () => res(true); c.postMessage({ op: 'clear' }, [ch.port2]);
      setTimeout(() => res(false), 3000);
    }));
    ok('Service Worker: clear bestaetigt', cleared);
    const after = await b.evaluate(() => fetch('/_m/' + '0'.repeat(32), { headers: { Range: 'bytes=0-9' } }).then(r => r.status));
    ok('Service Worker: unbekannte Wiedergabe -> 404', after === 404, after);
  } catch (e) {
    console.log('Abbruch: ' + (e && e.stack || e)); res.push(false);
  } finally {
    await browser.close();
  }
  for (const [k, v] of Object.entries(PERF)) console.log('PERF ' + JSON.stringify({ messung: 'browser_videos', punkt: k, wert: v }));
  console.log(`\nBROWSER-VIDEOS ${res.filter(Boolean).length}/${res.length} - ` + (res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
