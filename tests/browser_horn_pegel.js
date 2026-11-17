// Gjallarhorn 2.5 (Befund klein-web P6): Sprechring fuer ein Geraet ohne Bild. Drei Personen ueber den Knoten (calls.horn_from
// 3), C hat keine Kamera (getUserMedia mit Bild -> NotFoundError, Anruf nur mit Ton). Der Knoten reicht den Pegel der Quelle
// (ssrc-audio-level) an jeden Empfaenger weiter; der Browser liest ihn per getSynchronizationSources (H.lvSeen) und zeigt
// den Sprechring am Platz der Person, die spricht - auch ohne Bild (frueher nur der Sprecher-BILD-Platz des Knotens).
// Laeuft aus tests/anrufe_horn_knoten.py, geht aber auch allein:
//   SAGA_BASE=http://host:port SAGA_USERS=u1,u2,u3 SAGA_PASS=... [SHOTS=dir] node tests/browser_horn_pegel.js
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
const USERS = (E.SAGA_USERS || '').split(',').filter(Boolean);
if (!BASE || USERS.length < 3 || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USERS (3), SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
const HUES = [0, 120, 240], FREQ = [300, 540, 780], L = 'ABC';
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

// Kamera/Mikrofon-Attrappe wie browser_horn_knoten.js; cam false: keine Kamera (NotFoundError wie ein Geraet ohne)
const FAKE = ([hue, freq, cam]) => {
  const md = navigator.mediaDevices;
  let ac = null, gain = null, aStream = null, vTrack = null;
  const video = () => {
    if (vTrack && vTrack.readyState === 'live') return vTrack.clone();
    const cv = document.createElement('canvas'); cv.width = 320; cv.height = 180;
    const g = cv.getContext('2d'); let f = 0;
    setInterval(() => { f++; g.fillStyle = `hsl(${hue} 85% 50%)`; g.fillRect(0, 0, 320, 180); g.fillStyle = '#fff'; g.fillRect((f * 7) % 160, 20, 16, 16); }, 1000 / 15);
    vTrack = cv.captureStream(15).getVideoTracks()[0];
    return vTrack;
  };
  const audio = () => {
    if (!ac) {
      ac = new AudioContext(); const o = ac.createOscillator(); o.frequency.value = freq; gain = ac.createGain(); gain.gain.value = 0.004;
      const d = ac.createMediaStreamDestination(); o.connect(gain).connect(d); o.start(); aStream = d.stream;
    }
    return aStream.getAudioTracks()[0].clone();
  };
  window.__hornGain = v => { if (gain) gain.gain.value = v; };
  md.getUserMedia = async c => {
    if (c && c.video && !cam) throw new DOMException('Requested device not found', 'NotFoundError');
    const s = new MediaStream(); if (c && c.audio) s.addTrack(audio()); if (c && c.video) s.addTrack(video()); return s;
  };
};

const RKF = u => path.join(os.tmpdir(), 'saga-browser-rk-' + BASE.replace(/\W+/g, '_') + '-' + u + '.txt');
async function chatKeys(q) {
  await q.goto(BASE + '/chats'); await q.waitForTimeout(2500);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim(); fs.writeFileSync(RKF(q.__user), rk, { mode: 0o600 });
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 90000 });
  }
  await q.waitForTimeout(800);
}
const BROWSERS = [];
async function session(i) {
  const origin = new URL(BASE).origin;
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin, '--ignore-certificate-errors',
           '--autoplay-policy=no-user-gesture-required', '--no-proxy-server', '--disable-features=WebRtcHideLocalIpsWithMdns'] });
  BROWSERS.push(br);
  const ctx = await br.newContext({ viewport: { width: 1100, height: 720 }, locale: 'de-DE', ignoreHTTPSErrors: true, colorScheme: 'dark' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.__user = USERS[i];
  p.on('pageerror', e => console.log('  [pageerror ' + USERS[i] + '] ' + e.message));
  await p.addInitScript(FAKE, [HUES[i], FREQ[i], i !== 2]);
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', USERS[i]);
  await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(500);
  return p;
}
const horn = p => p.evaluate(() => window.HKCalls && window.HKCalls.horn());
const dev = p => p.evaluate(() => window.HKChat.state().device);
async function api(p, m, url, body) { return p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, data: e.data, msg: e.message })), [m, url, body]); }
async function liveOk(ps) { for (const p of ps) await until(() => p.evaluate(() => !!(window.HKLive && window.HKLive.state().sse)), 15000, 200); }
async function shot(p, name) { if (!E.SHOTS) return; fs.mkdirSync(E.SHOTS, { recursive: true }); await p.screenshot({ path: path.join(E.SHOTS, name + '.png') }); }

(async () => {
  let rc = 1;
  try {
    const ps = [];
    for (let i = 0; i < 3; i++) ps.push(await session(i));
    for (const p of ps) await chatKeys(p);
    const devs = [];
    for (const p of ps) devs.push(await dev(p));
    ok('3 Personen mit eigenen Chat-Geraeten', devs.every(Boolean) && new Set(devs).size === 3, devs);
    const mk = await api(ps[0], 'POST', '/api/chats', { kind: 'free', members: USERS.slice(1), device: devs[0] });
    ok('freie Gruppe mit 3 Mitgliedern', mk.ok, mk);
    const gid = mk.r.chat.id;
    for (const p of ps) await p.goto(BASE + '/chats#c' + gid);
    await ps[0].waitForSelector('#ct-chat:not([hidden])'); await liveOk(ps);
    for (let i = 0; i < 3; i++) await api(ps[i], 'PUT', '/api/calls/caps', { device: devs[i], caps: await ps[i].evaluate(() => window.HKCalls.caps()) });
    await until(async () => ps[0].evaluate(() => { const v = document.getElementById('ct-call-v'); return v && !v.hidden; }), 10000);

    // --- Anruf: A startet, B und C (ohne Kamera) treten bei ------------------------------------------------------------
    await ps[0].click('#ct-call-v');
    const ha = await until(async () => { const h = await horn(ps[0]); return h && h.up && h; }, 20000);
    ok('A: Gruppe 3 ueber den Knoten (calls.horn_from 3)', ha && ha.node, ha || await horn(ps[0]));
    for (let i = 1; i < 3; i++) {
      const rang = await ps[i].waitForSelector('.cl-ring', { timeout: 20000 }).then(() => true, () => false);
      if (rang) await ps[i].click('.cl-ring .cl-big');
      else await ps[i].evaluate(id => window.HKCalls.join(id, 'member', true), ha.id);
      const h = await until(async () => { const x = await horn(ps[i]); return x && x.up && x; }, 25000);
      ok(`${L[i]}: beigetreten${i === 2 ? ' (ohne Kamera, nur Ton)' : ''}`, !!h, h || await horn(ps[i]));
    }
    const all = await until(async () => {
      const hs = await Promise.all(ps.map(horn));
      return hs.every(h => h && h.up && h.known.length === 2 && h.tiles.length === 2) && hs;
    }, 20000);
    ok('alle 3: verbunden, je 2 Kacheln', all, (await Promise.all(ps.map(horn))).map(h => h && { up: h.up, known: h.known.length, tiles: h.tiles.length }));
    const cTile = await until(async () => { const h = await horn(ps[0]); const t = h.tiles.find(x => x.dev === devs[2]); return t && !t.video && t; }, 10000);
    ok('C hat bei A eine Kachel ohne Bild', !!cTile, (await horn(ps[0])).tiles);

    // --- Sprechen: erst C (ohne Bild), dann A -------------------------------------------------------------------------
    for (const s of [2, 0]) {
      for (let i = 0; i < 3; i++) await ps[i].evaluate(v => window.__hornGain(v), i === s ? 0.6 : 0.004);
      const t0 = Date.now();
      const got = await until(async () => {
        const hs = await Promise.all(ps.map(horn));
        return hs.every((h, i) => i === s || (h.lvSeen && h.speaking.length === 1 && h.speaking[0] === devs[s]
          && h.tiles.some(t => t.dev === devs[s] && t.speaking))) && hs;
      }, 12000, 300);
      const hs = await Promise.all(ps.map(horn));
      ok(`${L[s]} spricht${s === 2 ? ' (ohne Bild)' : ''}: Sprechring nur an ${L[s]} bei den anderen, Pegel vom Knoten (lvSeen; ${Date.now() - t0} ms)`, !!got,
         hs.map((h, i) => i === s ? '-' : { lvSeen: h.lvSeen, spk: h.spk && h.spk.slice(0, 6), speaking: h.speaking.map(x => devs.indexOf(x)),
           levels: Object.fromEntries(Object.entries(h.levels).map(([d, v]) => [devs.indexOf(d), Math.round(v * 1000) / 1000])) }));
      if (s === 2) { await ps[0].waitForTimeout(400); await shot(ps[0], 'sprechring-ohne-bild-a'); await shot(ps[1], 'sprechring-ohne-bild-b'); }
    }
    // Ruhe: niemand laut -> kein Sprechring
    for (const p of ps) await p.evaluate(() => window.__hornGain(0.004));
    const quiet = await until(async () => { const hs = await Promise.all(ps.map(horn)); return hs.every(h => h.speaking.length === 0) && hs; }, 8000, 300);
    ok('alle leise: kein Sprechring', !!quiet, (await Promise.all(ps.map(horn))).map(h => h.speaking.map(x => devs.indexOf(x))));
    for (const p of ps) await p.click('.cl-ctl .cl-red').catch(() => {});
    await ps[0].waitForTimeout(1000);
    rc = res.every(Boolean) ? 0 : 1;
  } catch (e) {
    console.log('FAIL Ausnahme: ' + (e && e.stack || e));
    rc = 1;
  } finally {
    for (const b of BROWSERS) await b.close().catch(() => {});
  }
  console.log(`\nBROWSER-HORN-PEGEL ${res.filter(Boolean).length}/${res.length}` + (rc === 0 ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(rc);
})();
