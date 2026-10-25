// Browser-Ende-zu-Ende fuer Anrufe 2.4, Etappe E3 "Bildschirm v2" (static/calls.js) - zwei Playwright-Kontexte im headless
// Chromium mit Kamera/Mikrofon- und Bildschirm-Attrappe (getDisplayMedia liefert ein Testbild 1280x720 und "Fake audio"):
//   SAGA_BASE=http://host:port SAGA_USER=konto-a SAGA_PASS=... SAGA_USER2=konto-b SAGA_PASS2=... [SHOTS=/pfad] \
//   [PLAYWRIGHT=/pfad/zu/playwright] [SAGA_CALLS23=calls.js aus v2.3.0] [SAGA_CALLS_E0=calls.js aus E0] node tests/browser_screen2.js
// Ohne SAGA_CALLS23/SAGA_CALLS_E0 holt der Test die Dateien per git show (v2.3.0 bzw. feature/24-e0). WebRTC braucht eine
// Netz-Schnittstelle ausser lo (Chrome sammelt keine Loopback-Kandidaten).
// Geprueft: 2.4 <-> 2.4 Betriebsart B - Datenschutzhinweis vor dem ersten Teilen (abschaltbar), eigene m-Zeile mit
// contentHint "detail" ueber die Neuverhandlung, tracks/msid/screen_mid im signierten Body, Empfaenger zeigt Bildschirm gross
// und Kamera klein (Tauschen), Leisten "Du teilst deinen Bildschirm" / "… teilt den Bildschirm", Systemton gemischt,
// Bitrate-Regeln (Bildschirm 2,5 Mbit/s 15 fps maintain-resolution, Kamera daneben 300 kbit/s), <= 4 Signale je Start/Stopp,
// Wiederverwendung der m-Zeile (kein Wachstum), Teilen vom Angerufenen auf derselben m-Zeile, Ende ueber den Browser
// ("Teilen beenden" -> ended) und beim Auflegen. Kompatibilitaet: E0-Client (reneg ohne screen2) und 2.3-Client bekommen
// Betriebsart A (replaceTrack, keine Neuverhandlung) und teilen selbst wie bisher. Messung (Konzept §4.5): Bitrate, Bildrate
// und Aufloesung des Bildschirms beim Sender und Empfaenger, CPU des Browsers (beide Kontexte) ohne und mit Teilen.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os'), cp = require('child_process');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '';
const res = [], PERF = {};
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, 'screen2-' + name + '.png') }); } }
function gitShow(ref) { try { return cp.execFileSync('git', ['show', ref + ':static/calls.js'], { cwd: __dirname, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }); } catch (e) { return ''; } }
const JS23 = E.SAGA_CALLS23 ? fs.readFileSync(E.SAGA_CALLS23, 'utf8') : gitShow('v2.3.0');
const JSE0 = E.SAGA_CALLS_E0 ? fs.readFileSync(E.SAGA_CALLS_E0, 'utf8') : gitShow('feature/24-e0');

async function session(browser, user, pass, opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: 'dark' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  if (opts.js) await ctx.route('**/static/calls.js*', r => r.fulfill({ status: 200, contentType: 'application/javascript', body: opts.js }));
  await ctx.addInitScript(HOOK);
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
// Nur im Test: PeerConnections und Bildschirm-Streams merken; Statistik je mid (Bytes, Bilder, Groesse)
const HOOK = () => {
  const O = window.RTCPeerConnection; const all = [];
  window.RTCPeerConnection = function (cfg) { const pc = new O(cfg); all.push(pc); return pc; };
  window.RTCPeerConnection.prototype = O.prototype;
  const pcNow = () => all.filter(x => x.connectionState !== 'closed').pop();
  window.__pcStats = async () => {
    const pc = pcNow(), o = { in: {}, out: {} }; if (!pc) return o;
    (await pc.getStats()).forEach(s => {
      if ((s.type === 'inbound-rtp' || s.type === 'outbound-rtp') && (s.kind || s.mediaType) === 'video') {
        const d = s.type === 'inbound-rtp' ? o.in : o.out, k = s.mid != null ? String(s.mid) : '?';
        d[k] = { bytes: (s.bytesReceived || 0) + (s.bytesSent || 0), frames: s.framesDecoded || s.framesEncoded || 0, w: s.frameWidth || 0, h: s.frameHeight || 0,
                 fps: s.framesPerSecond || 0, limit: s.qualityLimitationReason || '' };
      }
    });
    return o;
  };
  window.__sdpLen = () => { const pc = pcNow(); return pc && pc.localDescription ? pc.localDescription.sdp.length : 0; };
  window.__dms = [];
  if (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {
    const g = navigator.mediaDevices.getDisplayMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getDisplayMedia = c => g(c).then(s => { window.__dms.push(s); return s; });
  }
  // Wie der Knopf "Teilen beenden" des Browsers: die Bildschirm-Spur endet
  window.__endShare = () => { const s = window.__dms[window.__dms.length - 1]; const t = s && s.getVideoTracks()[0]; if (!t) return false; t.stop(); t.dispatchEvent(new Event('ended')); return true; };
};
const st = p => p.evaluate(() => window.HKCalls && window.HKCalls.state());
const sc = p => p.evaluate(() => window.HKCalls && window.HKCalls.screen && window.HKCalls.screen());
const stats = p => p.evaluate(() => window.__pcStats());
async function until(fn, ms = 20000, step = 100) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const sentSum = s => Object.keys((s && s.v3 && s.v3.sent) || {}).filter(k => k !== 'ice').reduce((n, k) => n + s.v3.sent[k], 0);
// CPU aller Browser-Prozesse (Kinder dieses Node-Prozesses), in Ticks
function cpuTicks() {
  const kids = {}, all = fs.readdirSync('/proc').filter(x => /^\d+$/.test(x));
  const info = {};
  all.forEach(pid => { try { const f = fs.readFileSync('/proc/' + pid + '/stat', 'utf8'); const r = f.slice(f.lastIndexOf(')') + 2).split(' '); info[pid] = { pp: r[1], t: +r[11] + +r[12] }; } catch (e) { /* weg */ } });
  const mine = new Set([String(process.pid)]); let grew = true;
  while (grew) { grew = false; Object.keys(info).forEach(p => { if (!mine.has(p) && mine.has(info[p].pp)) { mine.add(p); grew = true; } }); }
  mine.delete(String(process.pid));
  let t = 0; mine.forEach(p => { t += info[p].t; }); return t;
}
// -> Prozent eines Kerns (CLK_TCK 100: Ticks je Sekunde = Prozent)
async function cpuPct(page, ms) { const a = cpuTicks(), t = Date.now(); await page.waitForTimeout(ms); return Math.round((cpuTicks() - a) / ((Date.now() - t) / 1000) * 10) / 10; }
async function rate(p, dir, mid, ms) {
  const a = (await stats(p))[dir][mid] || { bytes: 0, frames: 0 }; const t = Date.now(); await p.waitForTimeout(ms);
  const b = (await stats(p))[dir][mid] || { bytes: 0, frames: 0 }; const s = (Date.now() - t) / 1000;
  return { kbps: Math.round((b.bytes - a.bytes) * 8 / s / 1000), fps: Math.round((b.frames - a.frames) / s * 10) / 10, w: b.w, h: b.h, limit: b.limit };
}
async function share(p, expectHint) {
  await p.click('.cl-ctl .cl-round >> nth=2');
  if (expectHint !== false) await p.waitForSelector('.cl-hint', { timeout: 4000 }).catch(() => {});
  if (await p.isVisible('.cl-hint-go')) await p.click('.cl-hint-go');
}
async function ringAccept(b) { await b.waitForSelector('.cl-ring', { timeout: 15000 }).catch(() => {}); await b.click('.cl-ring .cl-big'); }
async function callVideo(a, b) {
  await a.click('#ct-call-v'); await ringAccept(b);
  const x = await until(async () => { const s = await st(a); return s && s.connected && s; });
  const y = await until(async () => { const s = await st(b); return s && s.connected && s; });
  return x && y;
}
async function hang(a, b) { await a.click('.cl-ctl .cl-red'); await until(() => b.evaluate(() => !window.HKCalls.state()), 8000); await a.waitForTimeout(4200); }

(async () => {
  const origin = new URL(BASE).origin;
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin,
           '--auto-select-desktop-capture-source=Entire screen', '--autoplay-policy=no-user-gesture-required']
      .concat(E.FAKE_Y4M ? ['--use-file-for-fake-video-capture=' + E.FAKE_Y4M] : []) });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  try {
    const a = await session(browser, U1, E.SAGA_PASS);
    // Bughunt WV13: mit calls.horn_all laufen 1:1- und kleine Gruppenanrufe ueber einen Knoten - dieser Test prueft den
    // direkten Weg (Mesh) und wuerde ohne Fehler rot. Dann klar ueberspringen.
    if ((await a.evaluate(() => fetch('/api/calls/config', { headers: { 'X-Saga': '1' } }).then(r => r.json()).catch(() => ({})))).horn_dm) {
      console.log('SKIP browser_screen2: calls.horn_all ist an (Anrufe ueber Knoten) - der Mesh-Test gilt hier nicht. Fuer den Lauf "Alle Anrufe ueber Knoten" im Admin ausschalten.');
      process.exit(0);
    }
    let b = await session(browser, U2, E.SAGA_PASS2);
    await chatKeys(a, U1); await chatKeys(b, U2);
    const cfg = await a.evaluate(() => window.HK.api('GET', '/api/calls/config'));
    ok('Server meldet features screen2 (neben reneg, pip_hint)', (cfg.features || []).indexOf('screen2') >= 0 && (cfg.features || []).indexOf('reneg') >= 0, cfg.features);
    const R = await a.evaluate(() => { const r = window.HKCalls.rules; return {
      d: r('screen', 'direct'), u: r('screen', 'relay-udp'), t: r('screen', 'relay-tcp'), camD: r('cam', 'direct', 'large', 2, 720, true),
      camT: r('cam', 'relay-tcp', 'large', 2, 720, true), camSmall: r('cam', 'direct', 'small', 2, 720, true), camNo: r('cam', 'direct', 'large', 2, 720, false) }; });
    ok('Regeln Bildschirm je Weg (§1.3): 2,5 / 1,8 / 1,2 Mbit/s, 15 / 15 / 8 fps, immer maintain-resolution, volle Aufloesung',
      R.d.maxBitrate === 2500000 && R.u.maxBitrate === 1800000 && R.t.maxBitrate === 1200000 && R.d.maxFramerate === 15 && R.u.maxFramerate === 15
      && R.t.maxFramerate >= 5 && R.t.maxFramerate <= 10 && [R.d, R.u, R.t].every(x => x.degradationPreference === 'maintain-resolution' && x.scaleResolutionDownBy === 1), R);
    ok('Kamera neben dem Bildschirm: <= 300 kbit/s (direkt), ueber Relais TCP aus; ohne Teilen unveraendert 1,5 Mbit/s',
      R.camD.maxBitrate === 300000 && R.camD.active === true && R.camT.active === false && R.camSmall.maxBitrate <= 300000 && R.camNo.maxBitrate === 1500000, R);
    ok('Summe je Richtung ueber Relais TCP <= 1,6 Mbit/s (Bildschirm 1,2 + Kamera aus + Ton 24 kbit/s)', R.t.maxBitrate + 24000 <= 1600000);

    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
    await b.goto(BASE + '/chats'); await b.waitForTimeout(2000);

    // --- 1. 2.4 <-> 2.4: Betriebsart B --------------------------------------------------------------------------------
    ok('Videoanruf 2.4 <-> 2.4 verbunden', await callVideo(a, b));
    const ca = await st(a), cb = await st(b);
    ok('caps beider Seiten mit screen2', ca.v3.caps.indexOf('screen2') >= 0 && ca.v3.peerCaps.indexOf('screen2') >= 0 && cb.v3.peerCaps.indexOf('screen2') >= 0, { a: ca.v3, b: cb.v3.peerCaps });
    ok('... Betriebsart B moeglich (beide reneg + screen2)', (await sc(a)).caps && (await sc(b)).caps);
    await a.waitForTimeout(5000);
    PERF.cpu_browser_ohne_teilen_pct = await cpuPct(a, 6000);
    const s0a = await st(a), s0b = await st(b), n0 = s0a.v3.n, ml0 = (await sc(a)).mlines;
    await a.click('.cl-ctl .cl-round >> nth=2');
    ok('Vor dem ersten Teilen: Datenschutzhinweis (Benachrichtigungen, andere Fenster sichtbar; nichts gespeichert)',
      await until(async () => (await sc(a)).hintOpen, 4000) && /Benachrichtigungen/.test(await a.textContent('.cl-hint')) && /andere Fenster/.test(await a.textContent('.cl-hint'))
      && /nicht.*gespeichert|Nichts wird aufgezeichnet/.test(await a.textContent('.cl-hint')));
    await shot(a, '01-hinweis');
    await a.click('.cl-hint-no');
    ok('... Abbrechen: es wird nichts geteilt', !(await sc(a)).hintOpen && !(await sc(a)).sharing && (await a.evaluate(() => window.__dms.length)) === 0);
    await a.click('.cl-ctl .cl-round >> nth=2');
    await a.waitForSelector('.cl-hint-go');
    await a.check('.cl-hint-c input'); await a.click('.cl-hint-go');
    let t0 = Date.now();
    const sa = await until(async () => { const x = await sc(a); return x.sharing && x.screenMid && x.scrCur === 'sendonly' && x; }, 10000, 50);
    const sb = await until(async () => { const x = await sc(b); return x.big === 'screen' && x.small === 'cam' && x; }, 15000, 50);
    PERF.teilen_start_bis_bild_ms = Date.now() - t0;
    ok('Betriebsart B: eigene m-Zeile (3 m-Zeilen, ausgehandelt sendonly), contentHint detail, neues Angebot (n+1)', sa && sa.mode === 'track' && sa.mlines === ml0 + 1 && sa.hint === 'detail'
      && (await st(a)).v3.n === n0 + 1, sa || await sc(a));
    ok('Empfaenger: Bildschirm gross, Kamera klein', sb, sb || await sc(b));
    ok('Zuordnung ueber die mid: screen_mid im state = Rolle "screen" der m-Zeile im signierten Angebot, msid verschieden',
      sb && sa && sb.remoteMid === sa.screenMid && sb.peerTracks[sa.screenMid] === 'screen' && Object.values(sb.peerTracks).indexOf('cam') >= 0
      && sa.msid[sa.screenMid] && Object.keys(sa.msid).some(m => sa.mine[m] === 'cam' && sa.msid[m] !== sa.msid[sa.screenMid]), { sa, sb });
    PERF.sdp_3_mzeilen_zeichen = await a.evaluate(() => window.__sdpLen());
    ok('Leiste beim Teilenden: "Du teilst deinen Bildschirm" (rot, mit Beenden)', /Du teilst deinen Bildschirm/.test(sa.bar) && await a.isVisible('.cl-share.me .cl-share-stop'), sa.bar);
    ok('Leiste beim Empfaenger: "… teilt den Bildschirm"', await until(async () => /teilt den Bildschirm/.test((await sc(b)).bar), 4000), (await sc(b)).bar);
    ok('Systemton (Attrappe "Fake audio") in die Tonspur gemischt, Leiste nennt den Ton, Gegenueber erfaehrt es (screen_audio)',
      sa.audio && sa.audioTrack === 'mix' && /und den Ton/.test(sa.bar) && await until(async () => (await st(b)).remote.audio === true, 4000), sa);
    await a.waitForTimeout(2600);
    const r1 = (await st(a)).v3.rates;
    ok('Sende-Parameter: Bildschirm 2,5 Mbit/s, 15 fps, maintain-resolution; Kamera daneben 300 kbit/s', r1.screen && r1.screen.maxBitrate === 2500000
      && r1.screen.maxFramerate === 15 && r1.screen.degradationPreference === 'maintain-resolution' && r1.video && r1.video.maxBitrate === 300000, r1);
    const sig1 = sentSum(await st(a)) - sentSum(s0a) + sentSum(await st(b)) - sentSum(s0b);
    PERF.signale_start = sig1;
    ok(`<= 4 Signale je Start (offer, answer, state): ${sig1}`, sig1 <= 4 && sig1 >= 3, { a: (await st(a)).v3.sent, b: (await st(b)).v3.sent });
    await shot(b, '02-empfaenger-gross-klein'); await shot(a, '03-teilender-leiste');
    // Messung (Konzept §4.5): eingeschwungen
    await a.waitForTimeout(5000);
    const m = sa.screenMid;
    const out = await rate(a, 'out', m, 5000), inn = await rate(b, 'in', m, 5000), cam = await rate(b, 'in', Object.keys(sa.mine).find(k => sa.mine[k] === 'cam'), 4000);
    PERF.bildschirm_senden = out; PERF.bildschirm_empfang = inn; PERF.kamera_daneben_empfang = cam;
    PERF.cpu_browser_beim_teilen_pct = await cpuPct(a, 6000);
    ok(`Bildschirm fliesst: ${inn.kbps} kbit/s, ${inn.fps} fps, ${inn.w}x${inn.h} beim Empfaenger (<= 2,5 Mbit/s, <= 15 fps, Aufloesung bleibt)`,
      inn.kbps > 20 && inn.kbps <= 2600 && inn.fps > 0 && inn.fps <= 16 && inn.h >= 700, inn);
    ok(`Kamera daneben <= 300 kbit/s beim Empfaenger: ${cam.kbps}`, cam.kbps > 0 && cam.kbps <= 330, cam);
    // Tauschen
    await b.click('.cl-share .cl-swap');
    ok('Empfaenger tauscht: Kamera gross, Bildschirm klein', await until(async () => { const x = await sc(b); return x.big === 'cam' && x.small === 'screen'; }, 3000));
    await shot(b, '04-getauscht');
    await b.click('.cl-remote2');
    ok('... Klick aufs kleine Bild tauscht zurueck', await until(async () => { const x = await sc(b); return x.big === 'screen' && x.small === 'cam'; }, 3000));
    // Stopp ueber die Leiste
    const s1a = await st(a), s1b = await st(b);
    await a.click('.cl-share-stop');
    const sx = await until(async () => { const x = await sc(a); return !x.sharing && x.scrDir === 'recvonly' && x.scrCur === 'inactive' && x; }, 10000, 50);
    ok('Stopp: Spur weg, m-Zeile inaktiv (bleibt bestehen), Ton wieder nur Mikrofon', sx && sx.mlines === ml0 + 1 && sx.audioTrack === 'mic' && !sx.bar, sx || await sc(a));
    const sy = await until(async () => { const x = await sc(b); const y = await st(b); return !y.remote.screen && x.big === 'cam' && !x.small && !x.bar && x; }, 8000);
    ok('... Empfaenger: wieder nur Kamera, keine Leiste', sy, await sc(b));
    await a.waitForTimeout(2600);
    ok('... Kamera wieder 1,5 Mbit/s', (await st(a)).v3.rates.video.maxBitrate === 1500000, (await st(a)).v3.rates);
    const sig2 = sentSum(await st(a)) - sentSum(s1a) + sentSum(await st(b)) - sentSum(s1b);
    PERF.signale_stopp = sig2;
    ok(`<= 4 Signale je Stopp: ${sig2}`, sig2 <= 4, { a: (await st(a)).v3.sent, b: (await st(b)).v3.sent });
    // Zweites Teilen: kein Hinweis mehr (abgehakt), dieselbe m-Zeile
    await share(a, false);
    ok('Zweites Teilen: kein Hinweis (abgehakt), m-Zeile wiederverwendet (keine neue)', await until(async () => { const x = await sc(a); return x.sharing && x.scrCur === 'sendonly' && x.mlines === ml0 + 1 && x.starts === 2; }, 10000)
      && !(await sc(a)).hintOpen, await sc(a));
    ok('... Empfaenger zeigt wieder den Bildschirm', await until(async () => (await sc(b)).big === 'screen', 10000));
    ok('Ende ueber den Browser ("Teilen beenden" -> ended): Teilen endet sauber', await a.evaluate(() => window.__endShare())
      && await until(async () => { const x = await sc(a); return !x.sharing && x.scrCur === 'inactive'; }, 10000)
      && await until(async () => !(await st(b)).remote.screen, 6000));
    // Der Angerufene teilt: dieselbe m-Zeile in Gegenrichtung
    await share(b);
    const bs = await until(async () => { const x = await sc(b); return x.sharing && x.mode === 'track' && x.scrCur === 'sendonly' && x; }, 10000);
    ok('Angerufener teilt: Hinweis bei ihm, Betriebsart B auf derselben m-Zeile (keine vierte)', bs && bs.mlines === ml0 + 1 && bs.screenMid === sa.screenMid, bs || await sc(b));
    ok('... Anrufer zeigt den Bildschirm gross', await until(async () => { const x = await sc(a); return x.big === 'screen' && x.small === 'cam'; }, 10000), await sc(a));
    // Beide teilen gleichzeitig
    await share(a, false);
    ok('Beide teilen gleichzeitig (m-Zeile sendrecv in beide Richtungen)', await until(async () => {
      const x = await sc(a), y = await sc(b); return x.sharing && y.sharing && x.scrCur === 'sendrecv' && y.scrCur === 'sendrecv' && x.big === 'screen' && y.big === 'screen';
    }, 10000), { a: await sc(a), b: await sc(b) });
    await shot(a, '05-beide-teilen');
    // Auflegen waehrend des Teilens: alles aus
    await a.click('.cl-ctl .cl-red');
    ok('Auflegen beim Teilen: Gegenueber sieht das Ende', await until(() => b.evaluate(() => !window.HKCalls.state()), 8000));
    const allEnded = p => p.evaluate(() => window.__dms.length > 0 && window.__dms.every(s => s.getTracks().every(t => t.readyState === 'ended')));
    ok('... alle Bildschirm-Spuren beendet (beide Seiten)', await until(async () => (await allEnded(a)) && (await allEnded(b)), 5000));
    await a.waitForTimeout(4200);

    // --- 2. Kompatibilitaet: E0-Client und 2.3-Client --------------------------------------------------------------------
    for (const [label, js] of [['E0', JSE0], ['2.3', JS23]]) {
      ok(`${label}-Client verfuegbar (ohne screen2)`, js.length > 1000 && js.indexOf("'screen2'") < 0);
      if (!js) continue;
      const bo = await session(browser, U2, E.SAGA_PASS2, { js });
      await chatKeys(bo, U2); await b.close(); b = bo;
      await b.goto(BASE + '/chats'); await b.waitForTimeout(2000);
      ok(`${label}: Anruf verbunden`, await callVideo(a, b));
      const x0 = await st(a);
      ok(`${label}: Gegenueber ohne screen2 -> keine Betriebsart B`, x0.v3.peerCaps.indexOf('screen2') < 0 && !(await sc(a)).caps, x0.v3.peerCaps);
      await share(a, false);
      const xa = await until(async () => { const x = await sc(a); return x.sharing && x; }, 8000);
      ok(`${label}: Teilen als Betriebsart A (replaceTrack, 2 m-Zeilen, kein Angebot)`, xa && xa.mode === 'replace' && xa.mlines === 2 && !(await st(a)).v3.sent.offer && (await st(a)).v3.n === 1, xa);
      ok(`${label}: Gegenueber bekommt screen=true und Video`, await until(async () => { const y = await st(b); return y && y.remote.screen && y.bytesIn > 0; }, 8000));
      await a.waitForTimeout(2600);
      const ra = (await st(a)).v3.rates.video;
      ok(`${label}: Bildschirm auf dem Kamera-Sender mit Bildschirm-Regeln (2,5 Mbit/s, maintain-resolution)`, ra && ra.kind === 'screen' && ra.maxBitrate === 2500000, ra);
      await shot(b, '06-' + label + '-empfaengt');
      await a.click('.cl-share-stop');
      ok(`${label}: Stopp -> Kamera zurueck, screen=false beim Gegenueber`, await until(async () => { const y = await st(b); return y && !y.remote.screen; }, 6000)
        && !(await sc(a)).sharing && !(await st(a)).v3.sent.offer);
      // Alter Client teilt selbst (replaceTrack) -> 2.4 zeigt den Bildschirm gross (ohne screen_mid)
      await b.click('.cl-ctl .cl-round >> nth=2');
      ok(`${label}-Client teilt: 2.4 zeigt den Bildschirm gross, ohne kleines Bild, mit Leiste`, await until(async () => {
        const x = await sc(a); return x.big === 'screen' && !x.small && x.remoteMid == null && /teilt den Bildschirm/.test(x.bar);
      }, 8000), await sc(a));
      await shot(a, '07-' + label + '-teilt');
      await b.click('.cl-ctl .cl-round >> nth=2');
      ok(`${label}-Client beendet: 2.4 zeigt wieder die Kamera`, await until(async () => { const x = await sc(a); return x.big === 'cam' && !x.bar; }, 8000), await sc(a));
      await hang(a, b);
    }
  } catch (e) { console.error(e); res.push(false); }
  await browser.close();
  console.log('PERF ' + JSON.stringify(PERF));
  console.log(`\nBROWSER-BILDSCHIRM-V2 ${res.filter(Boolean).length}/${res.length}`, res.every(Boolean) ? '- ALLES GRUEN' : '- FEHLER');
  process.exit(res.every(Boolean) ? 0 : 1);
})();
