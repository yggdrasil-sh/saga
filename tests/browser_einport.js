// Browser-Ende-zu-Ende "Ein Name, ein Port" (Saga 2.5): Saga und der eingebaute Knoten unter einem Namen und einem Port.
// Laeuft aus tests/einport.py (eigene Instanz: saga-media mit SAGA_TLS=1, echter gjallarhorn), geht aber auch allein:
//   SAGA_BASE=https://saga.einport.test:PORT SAGA_HOST_RULES='MAP saga.einport.test 127.0.0.1' SAGA_USERS=u1,u2,u3
//   SAGA_PASS=... SAGA_WANT=turn|tcp [PLAYWRIGHT=...] node tests/browser_einport.js
// Drei Personen (eigene Chromium), freie Gruppe, Videoanruf ueber den Knoten (Admin: "Ueber Knoten ab 3"). Geprueft:
// Anmeldung und Live-Kanal ueber den TLS-Eingang, Kandidaten-URL = Saga-Ursprung, Weg wie erwartet (SAGA_WANT: turn hinter
// einer TLS-Durchreiche mit PROXY-Kopf - ohne den Umweg ueber ICE-TCP; tcp ohne Proxy), alle sehen alle (Farbe je Kachel).
// Messung: Klick bis beide Leitungen verbunden (PERF).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE, WANT = E.SAGA_WANT || 'turn';
const USERS = (E.SAGA_USERS || '').split(',').filter(Boolean);
if (!BASE || USERS.length < 3 || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USERS (3), SAGA_PASS setzen'); process.exit(2); }
const res = [], PERF = {};
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
const HUES = [0, 120, 240];
async function until(fn, ms = 20000, step = 200) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

const FAKE = (hue) => {
  const md = navigator.mediaDevices;
  let ac = null, aStream = null, vTrack = null;
  const video = () => {
    if (vTrack && vTrack.readyState === 'live') return vTrack.clone();
    const cv = document.createElement('canvas'); cv.width = 320; cv.height = 180;
    const g = cv.getContext('2d'); let f = 0;
    setInterval(() => { f++; g.fillStyle = `hsl(${hue} 85% 50%)`; g.fillRect(0, 0, 320, 180); g.fillStyle = '#fff'; g.fillRect((f * 7) % 280, 20, 20, 20); }, 1000 / 15);
    vTrack = cv.captureStream(15).getVideoTracks()[0];
    return vTrack;
  };
  const audio = () => {
    if (!ac) { ac = new AudioContext(); const o = ac.createOscillator(); const gn = ac.createGain(); gn.gain.value = 0.02; const d = ac.createMediaStreamDestination(); o.connect(gn).connect(d); o.start(); aStream = d.stream; }
    return aStream.getAudioTracks()[0].clone();
  };
  md.getUserMedia = async c => { const s = new MediaStream(); if (c && c.audio) s.addTrack(audio()); if (c && c.video) s.addTrack(video()); return s; };
};
const EYES = () => {
  const cv = document.createElement('canvas'); cv.width = 32; cv.height = 18;
  const g = cv.getContext('2d', { willReadFrequently: true }), out = {};
  document.querySelectorAll('.cl-tile[data-dev]').forEach(t => {
    const v = t.querySelector('video');
    if (!v || !v.videoWidth) { out[t.dataset.dev] = null; return; }
    g.drawImage(v, 0, 0, 32, 18);
    const [r, gg, b] = g.getImageData(28, 15, 1, 1).data, mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
    if (mx - mn < 30) { out[t.dataset.dev] = -1; return; }
    let h; if (mx === r) h = ((gg - b) / (mx - mn)) % 6; else if (mx === gg) h = (b - r) / (mx - mn) + 2; else h = (r - gg) / (mx - mn) + 4;
    out[t.dataset.dev] = Math.round((h * 60 + 360) % 360);
  });
  return out;
};
const hueOk = (a, b) => a != null && a >= 0 && Math.min(Math.abs(a - b), 360 - Math.abs(a - b)) < 25;
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
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true,
    args: [...(E.SAGA_HOST_RULES ? ['--host-resolver-rules=' + E.SAGA_HOST_RULES] : []), '--use-fake-ui-for-media-stream', '--ignore-certificate-errors',
           '--autoplay-policy=no-user-gesture-required', '--no-proxy-server', '--disable-features=WebRtcHideLocalIpsWithMdns'] });
  BROWSERS.push(br);
  const ctx = await br.newContext({ viewport: { width: 1000, height: 680 }, locale: 'de-DE', ignoreHTTPSErrors: true });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.__user = USERS[i];
  p.on('pageerror', e => console.log('  [pageerror ' + USERS[i] + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + USERS[i] + '] ' + m.text()));
  await p.addInitScript(FAKE, HUES[i]);
  await p.goto(BASE + '/login');
  await p.waitForLoadState('networkidle').catch(() => {});   // Passkey-Optionen (setzt die Sitzung) vor dem Absenden fertig
  await p.fill('input[name=username], input#username, input[type=text]', USERS[i]);
  await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(500);
  return p;
}
const horn = p => p.evaluate(() => window.HKCalls && window.HKCalls.horn());
const dev = p => p.evaluate(() => window.HKChat.state().device);
async function api(p, m, url, body) { return p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, data: e.data, msg: e.message })), [m, url, body]); }

(async () => {
  let rc = 1;
  try {
    const ps = [];
    for (let i = 0; i < 3; i++) ps.push(await session(i));
    const me = await api(ps[0], 'GET', '/api/account');
    ok('Anmeldung ueber den TLS-Eingang (' + new URL(BASE).host + ')', me.ok && JSON.stringify(me.r).indexOf('"' + USERS[0] + '"') >= 0, me);
    for (const p of ps) await chatKeys(p);
    const devs = [];
    for (const p of ps) devs.push(await dev(p));
    ok('3 Personen mit eigenen Chat-Geraeten', devs.every(Boolean) && new Set(devs).size === 3, devs);
    const hueOf = {}; devs.forEach((d, i) => { hueOf[d] = HUES[i]; });
    const mk = await api(ps[0], 'POST', '/api/chats', { kind: 'free', members: USERS.slice(1), device: devs[0] });
    ok('freie Gruppe mit 3 Mitgliedern', mk.ok, mk);
    const gid = mk.r.chat.id;
    for (const p of ps) await p.goto(BASE + '/chats#c' + gid);
    await ps[0].waitForSelector('#ct-chat:not([hidden])');
    const live = await until(() => ps[0].evaluate(() => !!(window.HKLive && window.HKLive.state().sse)), 15000);
    ok('Live-Kanal (SSE) ueber den TLS-Eingang', live);
    for (let i = 0; i < 3; i++) {
      const caps = await ps[i].evaluate(() => window.HKCalls.caps());
      await api(ps[i], 'PUT', '/api/calls/caps', { device: devs[i], caps });
    }
    await until(async () => ps[0].evaluate(() => { const v = document.getElementById('ct-call-v'); return v && !v.hidden; }), 10000);
    let t0 = Date.now();
    await ps[0].click('#ct-call-v');
    const ha = await until(async () => { const h = await horn(ps[0]); return h && h.up && h; }, 25000);
    PERF.start_klick_bis_verbunden_ms = Date.now() - t0;
    ok(`A: Anruf ueber den eingebauten Knoten, Weg ${WANT}, Knoten-Adresse = Saga-Ursprung`, ha && ha.path === WANT
       && ha.legs.audio.state === 'connected' && ha.legs.video.state === 'connected' && (!ha.url || new URL(ha.url).origin === new URL(BASE).origin),
       ha || await horn(ps[0]));
    const joinMs = [];
    for (let i = 1; i < 3; i++) {
      const p = ps[i];
      const rang = await p.waitForSelector('.cl-ring', { timeout: 20000 }).then(() => true, () => false);
      t0 = Date.now();
      if (rang) await p.click('.cl-ring .cl-big');
      else await p.evaluate(id => window.HKCalls.join(id, 'member', true), ha.id);
      const h = await until(async () => { const x = await horn(p); return x && x.up && x; }, 25000);
      joinMs.push(Date.now() - t0);
      ok(`${'ABC'[i]}: beigetreten, Weg ${WANT}`, h && h.path === WANT, h || await horn(p));
    }
    PERF.beitritt_klick_bis_verbunden_ms = joinMs;
    const eyes = await until(async () => {
      const e = await Promise.all(ps.map(p => p.evaluate(EYES)));
      return e.every((x, i) => devs.filter((d, j) => j !== i).every(d => hueOk(x[d], hueOf[d]))) && e;
    }, 30000, 500);
    ok('alle sehen alle (Farbe je Kachel)', !!eyes, eyes || await Promise.all(ps.map(p => p.evaluate(EYES))));
    if (E.SHOTS) { fs.mkdirSync(E.SHOTS, { recursive: true }); await ps[0].screenshot({ path: path.join(E.SHOTS, 'einport-anruf-' + WANT + '.png') }); }
    console.log('PERF ' + JSON.stringify(PERF));
    rc = res.every(Boolean) ? 0 : 1;
  } catch (e) {
    console.log('FAIL Ausnahme: ' + (e && e.stack || e));
  } finally {
    for (const b of BROWSERS) await b.close().catch(() => {});
  }
  console.log(res.every(Boolean) && res.length ? 'ALLES GRUEN' : 'FEHLER');
  process.exit(rc);
})();
