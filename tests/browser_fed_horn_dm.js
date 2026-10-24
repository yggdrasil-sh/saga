// Browser-Ende-zu-Ende 2.5: 1:1 UEBER KNOTEN UEBER SERVERGRENZEN ("Alle Anrufe ueber Knoten" auf Server A, Auftrag
// dmhorn). Eine Person auf A, eine auf B, je ein headless Chromium, Kamera/Mikrofon als Attrappe (Farbe und Ton je Person).
// Gedacht fuer echte Server (Testlabor, tools/testlab) - braucht nur zwei verbundene Saga-Instanzen mit bereitem Knoten
// (bzw. geteiltem Knoten) und calls.horn_all=1 auf A:
//   SAGA_A=https://a.<zone> SAGA_B=https://b.<zone> SAGA_USER_A=c1 SAGA_USER_B=c2 SAGA_PASS=... node tests/browser_fed_horn_dm.js
// Ablauf: Direkt-Chat A -> B (B nimmt die Anfrage an), A ruft per Video-Knopf an, B nimmt am Klingeln an - beide am
// Knoten, sehen (Farbe) und hoeren (Sprechring) sich, keine IP des Gegenuebers im Anruf; Auflegen beendet auf beiden
// Seiten. Zweiter Anruf: B lehnt ab -> bei A "abgelehnt". Optional SHOTS=<ordner>.
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, A = E.SAGA_A, B = E.SAGA_B;
if (!A || !B || !E.SAGA_USER_A || !E.SAGA_USER_B || !E.SAGA_PASS) { console.error('SAGA_A, SAGA_B, SAGA_USER_A, SAGA_USER_B, SAGA_PASS setzen'); process.exit(2); }
const res = [], PERF = {}, BROWSERS = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 3000))); }
async function shot(p, name) { if (E.SHOTS) { fs.mkdirSync(E.SHOTS, { recursive: true }); await p.screenshot({ path: path.join(E.SHOTS, 'fedhorn-dm-' + name + '.png') }); } }
const capped = (pr, ms = 15000) => { let t; return Promise.race([Promise.resolve(pr).finally(() => clearTimeout(t)), new Promise(r => { t = setTimeout(() => r(undefined), ms); })]); };
async function until(fn, ms = 20000, step = 300) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await capped(fn()); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const HUES = [0, 180], FREQ = [300, 660], L = 'AB';

const FAKE = ([hue, freq, w, h, fps]) => {
  const md = navigator.mediaDevices;
  let ac = null, gain = null, aStream = null, vTrack = null;
  const canvas = () => {
    const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
    const g = cv.getContext('2d'); let f = 0;
    setInterval(() => {
      f++; g.fillStyle = `hsl(${hue} 85% 50%)`; g.fillRect(0, 0, w, h);
      const x = (f * 7) % (w / 2);
      for (let i = 0; i < 16; i++) { const v = (Math.random() * 255) | 0; g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(10 + x + (i % 8) * (w / 40), 10 + ((i / 8) | 0) * (h / 22), w / 40, h / 22); }
    }, 1000 / fps);
    return cv.captureStream(fps).getVideoTracks()[0];
  };
  const video = () => { if (vTrack && vTrack.readyState === 'live') return vTrack.clone(); vTrack = canvas(); return vTrack; };
  const audio = () => {
    if (!ac) {
      ac = new AudioContext(); const o = ac.createOscillator(); o.frequency.value = freq; gain = ac.createGain(); gain.gain.value = 0.004;
      const d = ac.createMediaStreamDestination(); o.connect(gain).connect(d); o.start(); aStream = d.stream;
    }
    return aStream.getAudioTracks()[0].clone();
  };
  window.__hornGain = v => { if (gain) gain.gain.value = v; };
  md.getUserMedia = async c => { const s = new MediaStream(); if (c && c.audio) s.addTrack(audio()); if (c && c.video) s.addTrack(video()); return s; };
};
const EYES = () => {
  const cv = document.createElement('canvas'); cv.width = 32; cv.height = 18;
  const g = cv.getContext('2d', { willReadFrequently: true }), out = {};
  const hue = v => {
    if (!v || !v.videoWidth) return null;
    g.drawImage(v, 0, 0, 32, 18);
    const [r, gg, b] = g.getImageData(28, 15, 1, 1).data, mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
    if (mx - mn < 30) return -1;
    let hh; if (mx === r) hh = ((gg - b) / (mx - mn)) % 6; else if (mx === gg) hh = (b - r) / (mx - mn) + 2; else hh = (r - gg) / (mx - mn) + 4;
    return Math.round((hh * 60 + 360) % 360);
  };
  document.querySelectorAll('.cl-tile[data-dev]').forEach(t => { out[t.dataset.dev] = t.classList.contains('has-video') ? hue(t.querySelector('video')) : null; });
  return out;
};
const hueOk = (a, b) => a != null && a >= 0 && Math.min(Math.abs(a - b), 360 - Math.abs(a - b)) < 25;

async function session(i, base, user) {
  const br = await PW.chromium.launch({ ...(E.CHROMIUM ? { executablePath: E.CHROMIUM } : { channel: E.PW_CHANNEL || 'chromium' }), headless: true,
    args: ['--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required', '--no-sandbox', '--disable-features=WebRtcHideLocalIpsWithMdns'] });
  BROWSERS.push(br);
  const ctx = await br.newContext({ viewport: { width: 1000, height: 640 }, locale: 'de-DE' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: base });
  const p = await ctx.newPage();
  p.__base = base; p.__user = user; p.__cons = [];
  p.on('pageerror', e => console.log('  [pageerror ' + L[i] + '] ' + e.message));
  p.on('console', m => { const t = m.text(); if (E.DEBUG) console.log('  [console ' + L[i] + '] ' + t); if (/Content Security|CORS|blocked|refused/i.test(t) && p.__cons.length < 5) p.__cons.push(t.slice(0, 300)); });
  await p.addInitScript(FAKE, [HUES[i], FREQ[i], 320, 180, 10]);
  await p.goto(base + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  return p;
}
const RKF = p => path.join(os.tmpdir(), 'saga-fedhorn-dm-rk-' + p.__base.replace(/\W+/g, '_') + '-' + p.__user + '.txt');
async function chatKeys(q) {
  await q.goto(q.__base + '/chats'); await q.waitForTimeout(2500);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim(); fs.writeFileSync(RKF(q), rk, { mode: 0o600 });
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 90000 });
  }
  await q.waitForTimeout(800);
}
const api = (p, m, url, body) => p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, data: e.data, msg: e.message })), [m, url, body]);
const dev = p => p.evaluate(() => window.HKChat.state().device);
const horn = p => capped(p.evaluate(() => window.HKCalls && window.HKCalls.horn()).then(x => x, () => null));
const callOf = p => capped(p.evaluate(() => { const s = window.HKCalls && window.HKCalls.state(); return s ? JSON.parse(JSON.stringify(s)) : null; }).then(x => x, () => null));
async function chatByRef(p, ref) {
  return until(async () => { const r = await api(p, 'GET', '/api/chats?device=' + (await dev(p))); return r.ok && (r.r.chats || []).find(c => c.ref === ref); }, 30000, 500);
}
async function open(ps, chats) {
  for (let i = 0; i < 2; i++) { await ps[i].goto(ps[i].__base + '/chats'); await ps[i].goto(ps[i].__base + '/chats#c' + chats[i]); }
  await ps[0].waitForSelector('#ct-chat:not([hidden])');
  for (const p of ps) await until(() => p.evaluate(() => !!(window.HKLive && window.HKLive.state().sse)), 15000, 200);
  await until(() => ps[0].evaluate(() => { const v = document.getElementById('ct-call-v'); return v && !v.hidden; }), 15000);
}

(async () => {
  try {
    const ps = [await session(0, A, E.SAGA_USER_A), await session(1, B, E.SAGA_USER_B)];
    for (const p of ps) await chatKeys(p);
    const me = [];
    for (const p of ps) me.push(await until(async () => { const r = await api(p, 'GET', '/api/chat/v2/me'); return r.ok && !r.r.need && r.r.device_set_ok && r.r; }, 30000, 500));
    ok('Zertifikate v2 + signierte Geraete-Listen (A und B)', me.every(Boolean), me.map(x => !!x));
    const cfg = await api(ps[0], 'GET', '/api/calls/config');
    ok('A meldet horn_dm (Alle Anrufe ueber Knoten)', cfg.ok && cfg.r.horn_dm === true, cfg.r || cfg);
    const devs = [await dev(ps[0]), await dev(ps[1])];
    const d = await api(ps[0], 'POST', '/api/chats', { kind: 'dm', handle: me[1].handle, device: devs[0] });
    ok('A: Direkt-Chat mit der Person auf B', d.ok && d.r.chat && d.r.chat.v === 2, d);
    const cb = await chatByRef(ps[1], d.r.chat.ref);
    if (cb && cb.request) await api(ps[1], 'POST', '/api/chats/' + cb.id + '/request', { action: 'accept' });
    const chats = [d.r.chat.id, cb && cb.id];
    for (let i = 0; i < 2; i++) {
      const caps = await ps[i].evaluate(() => window.HKCalls.caps());
      await api(ps[i], 'PUT', '/api/calls/caps', { device: devs[i], caps });
    }
    // 1) Annehmen
    await open(ps, chats);
    let t0 = Date.now();
    await ps[0].click('#ct-call-v');
    const ha = await until(async () => { const h = await horn(ps[0]); return h && h.up && h; }, 30000);
    PERF.a_am_knoten_ms = Date.now() - t0;
    ok(`A ruft ueber den Knoten an (${ha && ha.node}, ${PERF.a_am_knoten_ms} ms)`, !!ha, ha || await horn(ps[0]));
    const rang = await ps[1].waitForSelector('.cl-ring', { timeout: 25000 }).then(() => true, () => false);
    PERF.klingeln_b_ms = Date.now() - t0;
    ok(`B klingelt (${PERF.klingeln_b_ms} ms ab Anruf)`, rang);
    await shot(ps[1], 'klingeln-b');
    t0 = Date.now();
    if (rang) await ps[1].click('.cl-ring .cl-big');
    const hb = await until(async () => { const h = await horn(ps[1]); return h && h.up && h; }, 40000);
    PERF.annahme_bis_knoten_ms = Date.now() - t0;
    ok(`B nimmt an, verbunden mit Knoten ${hb && hb.node} (${PERF.annahme_bis_knoten_ms} ms)`, !!hb, hb || { horn: await horn(ps[1]), console: ps[1].__cons });
    const both = await until(async () => { const hs = await Promise.all(ps.map(horn)); return hs.every(h => h && h.up && h.known.length === 1 && h.tiles.length === 1) && hs; }, 40000);
    ok('beide verbunden, Schluessel ausgetauscht, je eine Kachel', !!both, (await Promise.all(ps.map(horn))).map(h => h && { up: h.up, known: h.known.length, tiles: h.tiles.length, node: h.node }));
    t0 = Date.now();
    const seen = await until(async () => {
      const eyes = await Promise.all(ps.map(p => p.evaluate(EYES)));
      return hueOk(eyes[0][devs[1]], HUES[1]) && hueOk(eyes[1][devs[0]], HUES[0]) && eyes;
    }, 60000, 600);
    PERF.sehen_ms = Date.now() - t0;
    ok(`beide sehen sich (Farbe = Person, ${PERF.sehen_ms} ms)`, !!seen, seen || await Promise.all(ps.map(p => p.evaluate(EYES))));
    for (const s of [0, 1]) {
      for (let i = 0; i < 2; i++) await ps[i].evaluate(v => window.__hornGain(v), i === s ? 0.6 : 0.004);
      const o = 1 - s;
      const got = await until(async () => { const h = await horn(ps[o]); return h && h.speaking.indexOf(devs[s]) >= 0; }, 25000, 400);
      ok(`${L[s]} spricht - ${L[o]} hoert (Sprechring aus dem entschluesselten Ton)`, !!got, await horn(ps[o]));
    }
    for (let i = 0; i < 2; i++) await ps[i].evaluate(() => window.__hornGain(0.004));
    const st = await Promise.all(ps.map(p => capped(p.evaluate(() => window.HKCalls.hornStats()))));
    ok('keine gefaelschten oder unverschluesselten Rahmen (Frame-E2E)', st.every(x => { const d = (x && x.drop) || {}; return !(d.auth || d.format || d.codec); }), st.map(x => x && x.drop));
    const ca = await callOf(ps[0]);
    PERF.call = ca && { topo: ca.call && ca.call.topo, dm: ca.call && ca.call.dm };
    await shot(ps[0], 'gespraech-a'); await shot(ps[1], 'gespraech-b');
    await ps[1].evaluate(() => { const b = document.querySelector('.cl-ctl .cl-red'); if (b) b.click(); }).catch(() => {});
    const gone = await until(async () => (await Promise.all(ps.map(p => p.evaluate(() => !window.HKCalls.state())))).every(Boolean), 20000);
    ok('B legt auf - Anruf endet auf beiden Seiten', !!gone);
    // 2) Ablehnen
    await open(ps, chats);
    await ps[0].click('#ct-call-v');
    const rang2 = await ps[1].waitForSelector('.cl-ring', { timeout: 25000 }).then(() => true, () => false);
    ok('zweiter Anruf klingelt bei B', rang2);
    const dec = await ps[1].$('.cl-ring .cl-red, .cl-ring [data-act=decline]');
    if (dec) await dec.click(); else await ps[1].evaluate(() => { const s = window.HKCalls.state(); if (s && s.id) window.HK.api('POST', '/api/calls/' + s.id + '/decline', { reason: 'declined' }); });
    const end2 = await until(async () => !(await ps[0].evaluate(() => window.HKCalls.state())), 20000);
    ok('B lehnt ab - bei A endet der Anruf', !!end2);
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e));
  } finally {
    for (const br of BROWSERS) await br.close().catch(() => {});
  }
  console.log('PERF ' + JSON.stringify(PERF));
  console.log(`\nBROWSER-FED-HORN-DM ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
