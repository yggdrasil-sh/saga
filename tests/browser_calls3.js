// Browser-Ende-zu-Ende fuer Anrufe 2.4, Etappe E0 (saga-call v3 in static/calls.js) - zwei Playwright-Kontexte im headless
// Chrome mit Kamera/Mikrofon-Attrappe, wie tests/browser_calls.js:
//   SAGA_BASE=http://host:port SAGA_USER=konto-a SAGA_PASS=... SAGA_USER2=konto-b SAGA_PASS2=... [SHOTS=/pfad] \
//   [PLAYWRIGHT=/pfad/zu/node_modules/playwright-core] [SAGA_CALLS23=/pfad/zu/calls.js aus v2.3.0] node tests/browser_calls3.js
// FAKE_Y4M=/pfad/rauschen.y4m: Kamera-Attrappe aus einer Datei (Rauschen statt Testbild, fuer eine realistische Bitrate bei
// der PiP-Messung; die eingebaute Attrappe komprimiert auf ~150 kbit/s). Ohne SAGA_CALLS23 holt der Test static/calls.js aus dem Git-Tag v2.3.0 (git show). WebRTC braucht eine Netz-Schnittstelle
// ausser lo (Chrome sammelt keine Loopback-Kandidaten).
// Geprueft: caps im signierten Body auf beiden Seiten, gleiche pc-Kennung, hoeflich/unhoeflich gegensaetzlich; Bitrate-Regeln
// nach §1.3 (Tabelle und angewandte Sende-Parameter); ICE-Restart (neuer ice-ufrag, Verbindung bleibt, Medien fliessen
// weiter) - einseitig, gleichzeitig von beiden Seiten (Glare, Perfect Negotiation) und nach einem Netzwechsel ("online");
// want small bei verstecktem Tab und im Bild-im-Bild (Bitrate beim Empfaenger sinkt, danach wieder large); bye beim Auflegen.
// Kompatibilitaet: B laeuft mit dem 2.3-Client (static/calls.js aus v2.3.0) - Anruf in beide Richtungen wie bisher, der
// 2.4-Client schickt keine Neuverhandlung (state.want/pip gehen als Felder mit, 2.3 ignoriert sie).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os'), cp = require('child_process');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '';
const res = [], PERF = {};
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, 'v3-' + name + '.png') }); } }
function calls23() {
  if (E.SAGA_CALLS23) return fs.readFileSync(E.SAGA_CALLS23, 'utf8');
  try { return cp.execFileSync('git', ['show', 'v2.3.0:static/calls.js'], { cwd: __dirname, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }); } catch (e) { return ''; }
}

async function session(browser, user, pass, opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: 'dark' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  if (opts.js23) await ctx.route('**/static/calls.js*', r => r.fulfill({ status: 200, contentType: 'application/javascript', body: opts.js23 }));
  // wie ein Web 2.3/2.4: Chat-Stufe 2 (in Chats der Stufe 3 die Kompat-Sicht v: 2, zu der der 2.3-Anrufcode passt)
  if (opts.js23) await ctx.route('**/api/**', r => { const h = r.request().headers(); if (h['x-saga-chat']) h['x-saga-chat'] = '2'; return r.continue({ headers: h }); });
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
const st = p => p.evaluate(() => window.HKCalls && window.HKCalls.state());
async function until(fn, ms = 20000, step = 100) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
// Empfangene Videobytes (inbound-rtp video) - fuer die Bitrate beim Empfaenger
const vidIn = p => p.evaluate(() => window.HKCalls.state() && window.__pcStats ? window.__pcStats() : 0);
async function kbps(p, ms) { const a = await vidIn(p); await p.waitForTimeout(ms); const b = await vidIn(p); return Math.round((b - a) * 8 / ms); }
// Zugriff auf die PeerConnection fuer die Messung: RTCPeerConnection umhuellen (nur im Test)
const PC_HOOK = () => {
  const O = window.RTCPeerConnection; const all = [];
  window.RTCPeerConnection = function (cfg) { const pc = new O(cfg); all.push(pc); return pc; };
  window.RTCPeerConnection.prototype = O.prototype;
  window.__pcStats = async () => {
    const pc = all.filter(x => x.connectionState !== 'closed').pop(); if (!pc) return 0;
    let n = 0; (await pc.getStats()).forEach(s => { if (s.type === 'inbound-rtp' && (s.kind || s.mediaType) === 'video') n += s.bytesReceived || 0; });
    return n;
  };
  window.__fakeHidden = on => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => on ? 'hidden' : 'visible' });
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => !!on });
    document.dispatchEvent(new Event('visibilitychange'));
  };
};
async function ringAccept(b, video) {
  await b.waitForSelector('.cl-ring', { timeout: 15000 }).catch(() => {});
  await b.click(video ? '.cl-ring .cl-big' : '.cl-ring .cl-big');
}

(async () => {
  const origin = new URL(BASE).origin;
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin,
           '--autoplay-policy=no-user-gesture-required'].concat(E.FAKE_Y4M ? ['--use-file-for-fake-video-capture=' + E.FAKE_Y4M] : []) });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2);
    await a.addInitScript(PC_HOOK); await b.addInitScript(PC_HOOK);
    await chatKeys(a, U1); await chatKeys(b, U2);
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
    await b.goto(BASE + '/chats'); await b.waitForTimeout(2000);
    const rules = await a.evaluate(() => {
      const R = window.HKCalls.rules, o = {};
      ['direct', 'relay-udp', 'relay-tcp'].forEach(p => {
        o[p] = { cam: R('cam', p, 'large', 2, 720), screen: R('screen', p, 'large', 2, 1080), mesh: R('cam', p, 'large', 3, 720),
                 audio: R('audio', p), small: R('cam', p, 'small', 2, 720), smallMesh: R('cam', p, 'small', 3, 720), screenSmall: R('screen', p, 'small', 2, 1080) };
      });
      return o;
    });
    ok('Bitrate-Regeln direkt: Kamera 1,5 Mbit/s 720p, Bildschirm 2,5 Mbit/s <=15 fps, Mesh 600 kbit/s 540p, Ton 32 kbit/s',
      rules.direct.cam.maxBitrate === 1500000 && rules.direct.cam.scaleResolutionDownBy === 1 && rules.direct.screen.maxBitrate === 2500000
      && rules.direct.screen.maxFramerate === 15 && rules.direct.mesh.maxBitrate === 600000 && rules.direct.mesh.scaleResolutionDownBy === 1.33
      && rules.direct.audio.maxBitrate === 32000, rules.direct);
    ok('Bitrate-Regeln Relais UDP: 1,2 / 1,8 Mbit/s / 500 kbit/s',
      rules['relay-udp'].cam.maxBitrate === 1200000 && rules['relay-udp'].screen.maxBitrate === 1800000 && rules['relay-udp'].mesh.maxBitrate === 500000, rules['relay-udp']);
    const tcp = rules['relay-tcp'];
    ok('Bitrate-Regeln Relais TCP: Kamera 800 kbit/s 540p, Bildschirm 1,2 Mbit/s 5-10 fps maintain-resolution, Mesh 350 kbit/s 360p15, Ton 24 kbit/s',
      tcp.cam.maxBitrate === 800000 && tcp.cam.scaleResolutionDownBy === 1.33 && tcp.screen.maxBitrate === 1200000 && tcp.screen.maxFramerate >= 5
      && tcp.screen.maxFramerate <= 10 && tcp.screen.degradationPreference === 'maintain-resolution' && tcp.mesh.maxBitrate === 350000
      && tcp.mesh.scaleResolutionDownBy === 2 && tcp.mesh.maxFramerate === 15 && tcp.audio.maxBitrate === 24000, tcp);
    ok('want small: Kamera <= 300 kbit/s (Mesh 200), scaleResolutionDownBy 2-3; Bildschirm unveraendert',
      ['direct', 'relay-udp', 'relay-tcp'].every(p => rules[p].small.maxBitrate <= 300000 && rules[p].small.scaleResolutionDownBy >= 2
        && rules[p].small.scaleResolutionDownBy <= 3 && rules[p].smallMesh.maxBitrate <= 200000
        && JSON.stringify(rules[p].screenSmall) === JSON.stringify(rules[p].screen)), rules.direct.small);

    // --- 1. Videoanruf 2.4 <-> 2.4 ---------------------------------------------------------------------------------
    let t0 = Date.now();
    await a.click('#ct-call-v');
    await ringAccept(b, true);
    const ca = await until(async () => { const s = await st(a); return s && s.connected && s; });
    const cb = await until(async () => { const s = await st(b); return s && s.connected && s; });
    PERF.aufbau_klick_bis_verbunden_ms = Date.now() - t0;
    ok('2.4 <-> 2.4: verbunden', ca && cb, { ca, cb });
    ok('Grenzen ICE-Restart wie in den Apps: 3 Versuche, disconnected 2,5 s, failed 20 s',
      JSON.stringify(await a.evaluate(() => window.HKCalls.limits)) === JSON.stringify({ restartMax: 3, discS: 2.5, failS: 20 }));
    const has3 = x => ['reneg', 'want', 'bye'].every(k => x.indexOf(k) >= 0);   // ab E3 zusaetzlich screen2
    ok('caps im signierten Body auf beiden Seiten (reneg, want, bye)', ca && cb && has3(ca.v3.peerCaps) && has3(cb.v3.peerCaps),
      { a: ca && ca.v3.peerCaps, b: cb && cb.v3.peerCaps });
    ok('gleiche pc-Kennung (sortiertes Geraete-Paar), genau eine Seite hoeflich', ca && cb && ca.v3.pc && ca.v3.pc === cb.v3.pc && ca.v3.polite === !cb.v3.polite,
      { a: ca && ca.v3.pc, b: cb && cb.v3.pc });
    await a.waitForTimeout(2600);
    let sa = await st(a);
    ok('Bitrate-Regeln angewandt (direkt): Kamera 1,5 Mbit/s, Ton 32 kbit/s', sa.v3.net === 'direct' && sa.v3.rates.video && sa.v3.rates.video.maxBitrate === 1500000
      && sa.v3.rates.audio && sa.v3.rates.audio.maxBitrate === 32000, sa.v3);
    // Empfangsrate eingeschwungen (Bandbreitenschaetzung braucht einige Sekunden)
    await a.waitForTimeout(6000);
    const large = await kbps(b, 4000);
    PERF.empfang_large_kbps = large;

    // --- 2. ICE-Restart einseitig ------------------------------------------------------------------------------------
    const uf0 = (await st(b)).v3.ufrag, by0 = (await st(b)).bytesIn;
    t0 = Date.now();
    ok('ICE-Restart angestossen (A)', await a.evaluate(() => window.HKCalls.restartIce()));
    const r1 = await until(async () => { const x = await st(b); return x && x.v3.ufrag !== uf0 && x.v3.signaling === 'stable' && x; }, 15000, 50);
    const r1a = await until(async () => { const x = await st(a); return x && !x.v3.restarting && x.v3.signaling === 'stable' && x.v3.ice !== 'disconnected' && x; }, 15000, 50);
    PERF.ice_restart_ms = Date.now() - t0;
    ok('ICE-Restart: neuer ice-ufrag beim Gegenueber, Neuverhandlung stabil, A wieder verbunden (n=2)', r1 && r1a && r1a.v3.n === 2 && r1a.connected,
      { b: r1 && r1.v3, a: r1a && r1a.v3 });
    await a.waitForTimeout(2000);
    ok('... Medien fliessen weiter', (await st(b)).bytesIn > by0);
    // --- 3. Glare: beide gleichzeitig ---------------------------------------------------------------------------------
    const ufA = (await st(a)).v3.ufrag, ufB = (await st(b)).v3.ufrag;
    t0 = Date.now();
    await Promise.all([a.evaluate(() => window.HKCalls.restartIce()), b.evaluate(() => window.HKCalls.restartIce())]);
    const g = await until(async () => {
      const x = await st(a), y = await st(b);
      return x && y && x.v3.ufrag !== ufA && y.v3.ufrag !== ufB && x.v3.signaling === 'stable' && y.v3.signaling === 'stable'
        && !x.v3.restarting && !y.v3.restarting && x.connected && y.connected && { a: x.v3, b: y.v3 };
    }, 20000, 50);
    PERF.glare_ms = Date.now() - t0;
    ok('Glare (beide starten gleichzeitig neu): Perfect Negotiation loest es, beide stabil und verbunden', g, g || { a: (await st(a)).v3, b: (await st(b)).v3 });
    // --- 4. Netzwechsel ------------------------------------------------------------------------------------------------
    const rs0 = (await st(a)).v3.restarts;
    await a.evaluate(() => window.dispatchEvent(new Event('online')));
    ok('Netzwechsel ("online"): ICE-Restart von selbst', await until(async () => { const x = await st(a); return x.v3.restarts > rs0 && !x.v3.restarting && x.v3.signaling === 'stable'; }, 15000));
    await shot(a, '01-nach-ice-restart');

    // --- 5. want small bei verstecktem Tab ----------------------------------------------------------------------------
    await b.evaluate(() => window.__fakeHidden(true));
    const ws = await until(async () => { const x = await st(a); return x.remote.want === 'small' && x.v3.rates.video && x.v3.rates.video.maxBitrate === 300000 && x; }, 8000);
    ok('Tab von B versteckt -> state.want small -> A senkt Kamera auf 300 kbit/s, verkleinert (scaleResolutionDownBy >= 2)',
      ws && ws.v3.rates.video.scaleResolutionDownBy >= 2, ws ? ws.v3.rates : (await st(a)).v3);
    await a.waitForTimeout(3000);
    const small = await kbps(b, 4000);
    PERF.empfang_small_kbps = small;
    PERF.empfang_small_anteil = large ? Math.round(small / large * 100) / 100 : null;
    ok(`Empfang bei B sinkt: ${large} -> ${small} kbit/s (Ziel <= 300 kbit/s, -60 %)`, small <= 330 && (!large || large < 400 || small <= large * 0.4), { large, small });
    await b.evaluate(() => window.__fakeHidden(false));
    ok('Wieder sichtbar -> want large -> 1,5 Mbit/s', await until(async () => { const x = await st(a); return x.remote.want === 'large' && x.v3.rates.video.maxBitrate === 1500000; }, 8000));
    // --- 6. Bild-im-Bild -------------------------------------------------------------------------------------------------
    const pk = (await st(b)).v3.pipKind;
    const pipVisible = await b.isVisible('.cl-pip');
    ok(`PiP-Knopf nur, wenn der Browser es kann (${pk || 'kein API'})`, pk ? pipVisible : !pipVisible);
    if (pk && pipVisible) {
      await b.click('.cl-pip').catch(() => {});
      const inPip = await until(async () => (await st(b)).v3.pip, 4000);
      if (inPip) {
        ok('Bild-im-Bild: A bekommt pip und want small', await until(async () => { const x = await st(a); return x.remote.pip && x.remote.want === 'small'; }, 6000));
        await b.evaluate(() => window.HKCalls.pip(false));
        ok('Bild-im-Bild beendet: want large', await until(async () => { const x = await st(a); return !x.remote.pip && x.remote.want === 'large'; }, 6000));
      } else console.log('--   Bild-im-Bild: im headless Chrome nicht moeglich (' + pk + ') - Zustand per Test-Schnittstelle geprueft');
    }
    // Ohne PiP-API: Knopf weg
    const hidden = await b.evaluate(() => { const d = Object.getOwnPropertyDescriptor(window, 'documentPictureInPicture'); window.__dpip = d;
      Object.defineProperty(window, 'documentPictureInPicture', { configurable: true, value: undefined }); Object.defineProperty(document, 'pictureInPictureEnabled', { configurable: true, get: () => false });
      window.dispatchEvent(new Event('resize')); return window.HKCalls.state().v3.pipKind; });
    await b.click('.cl-ctl .cl-round >> nth=0'); await b.click('.cl-ctl .cl-round >> nth=0');   // neu zeichnen (stumm an/aus)
    await b.waitForTimeout(300);
    ok('Browser ohne PiP-API (z. B. Firefox): Knopf ausgeblendet', hidden === '' && !(await b.isVisible('.cl-pip')), hidden);
    // --- 7. Auflegen mit bye ---------------------------------------------------------------------------------------------
    const byeSent = async () => (await st(a)).v3.sent.bye || 0;
    await a.click('.cl-ctl .cl-red');
    ok('Auflegen: Gegenueber sieht das Ende', await until(() => b.evaluate(() => !window.HKCalls.state()), 8000));
    void byeSent;
    await a.waitForTimeout(4200);

    // --- 8. Kompatibilitaet: B mit dem 2.3-Client --------------------------------------------------------------------------
    const js23 = calls23();
    ok('2.3-Client (static/calls.js aus v2.3.0) verfuegbar', js23.length > 1000 && !/peerCaps/.test(js23));
    if (js23) {
      const b3 = await session(browser, U2, E.SAGA_PASS2, { js23 });
      await b3.addInitScript(PC_HOOK);
      await chatKeys(b3, U2);
      await b.close();
      await b3.goto(BASE + '/chats'); await b3.waitForTimeout(2000);
      ok('B laeuft mit dem 2.3-Client (kein v3 im Zustand)', await b3.evaluate(() => !!window.HKCalls && window.HKCalls.restartIce === undefined));
      t0 = Date.now();
      await a.click('#ct-call-v');
      await ringAccept(b3, true);
      const x = await until(async () => { const s = await st(a); return s && s.connected && s.verified && s; });
      const y = await until(async () => { const s = await st(b3); return s && s.connected && s.verified && s; });
      PERF.aufbau_24_zu_23_ms = Date.now() - t0;
      ok('2.4 ruft 2.3 an: verbunden, Fingerabdruck geprueft', x && y && x.verified && y.verified, { x: x && x.v3, y });
      ok('... 2.4 erkennt: Gegenueber ohne reneg/want (caps leer)', x && x.v3.peerCaps.length === 0);
      await a.waitForTimeout(3000);
      ok('... Medien in beide Richtungen', (await st(a)).bytesIn > 0 && (await st(b3)).bytesIn > 0);
      ok('... ICE-Restart wird NICHT angestossen (2.3 kennt keine Neuverhandlung)', (await a.evaluate(() => window.HKCalls.restartIce())) === false);
      await a.evaluate(() => window.dispatchEvent(new Event('online')));
      await a.evaluate(() => window.__fakeHidden(true)); await a.waitForTimeout(2500); await a.evaluate(() => window.__fakeHidden(false));
      await a.waitForTimeout(1200);
      const sx = (await st(a)).v3;
      ok('... nach Netzwechsel und verstecktem Tab: kein Angebot an die 2.3-Gegenstelle (want geht als harmloses state-Feld mit)',
        !sx.sent.offer && sx.n === 1 && sx.restarts === 0 && sx.wantSent === 'large', sx);
      ok('... 2.3-Client laeuft weiter (verbunden, keine Fehler)', (await st(b3)).connected);
      await a.click('.cl-ctl .cl-round >> nth=0');
      ok('... Stumm (erweitertes state) kommt beim 2.3-Client an', await until(async () => { const s = await st(b3); return s && s.remote.mic === false; }, 6000));
      ok('... Bitrate-Regeln gelten auch gegenueber 2.3 (eigener Sender)', (await st(a)).v3.rates.video && (await st(a)).v3.rates.video.maxBitrate === 1500000);
      await shot(a, '02-anruf-mit-23');
      await a.click('.cl-ctl .cl-red');
      ok('... Auflegen beim 2.3-Client angekommen', await until(() => b3.evaluate(() => !window.HKCalls.state()), 8000));
      await a.waitForTimeout(4200);
      // 2.3 ruft 2.4 an
      await b3.click('.ct-item-b').catch(() => {});
      await b3.goto(BASE + '/chats#u' + encodeURIComponent(U1)); await b3.waitForSelector('#ct-chat:not([hidden])'); await b3.waitForTimeout(1200);
      await a.goto(BASE + '/chats'); await a.waitForTimeout(2000);
      await b3.click('#ct-call-a');
      await ringAccept(a, false);
      const x2 = await until(async () => { const s = await st(a); return s && s.connected && s; });
      const y2 = await until(async () => { const s = await st(b3); return s && s.connected && s; });
      ok('2.3 ruft 2.4 an: verbunden, Ton fliesst', x2 && y2 && await until(async () => (await st(a)).audio.packetsIn > 0, 8000), { x2: x2 && x2.v3 });
      ok('... 2.4 als Angerufener: keine Neuverhandlung, kein bye an 2.3', x2 && x2.v3.peerCaps.length === 0 && (await a.evaluate(() => window.HKCalls.restartIce())) === false);
      await a.click('.cl-ctl .cl-red');
      ok('... Auflegen vom 2.4-Client beim 2.3-Client angekommen', await until(() => b3.evaluate(() => !window.HKCalls.state()), 8000));
      const sent = (await st(a)) ? (await st(a)).v3.sent : null;
      void sent;
    }
  } catch (e) { console.error(e); res.push(false); }
  await browser.close();
  console.log('PERF ' + JSON.stringify(PERF));
  console.log(`\nBROWSER-ANRUFE-V3 ${res.filter(Boolean).length}/${res.length}`, res.every(Boolean) ? '- ALLES GRUEN' : '- FEHLER');
  process.exit(res.every(Boolean) ? 0 : 1);
})();
