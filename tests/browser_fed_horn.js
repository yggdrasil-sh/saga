// Browser-Ende-zu-Ende Gjallarhorn P9/P10 UEBER SERVERGRENZEN: Gruppenanruf mit 3 Personen auf Server A (Heim) und 3 auf
// Server B, je ein headless Chromium, Kamera/Mikrofon als Attrappe (Farbe und Ton je Person). Laeuft aus
// tests/browser_fed_horn.py (zwei Saga-Instanzen mit je einem gekoppelten Knoten, Steuerung fuer den Fallwechsel auf
// CTL=http://127.0.0.1:<port>). Zwei Durchgaenge im selben Chat:
//   a) Kaskade: B hat einen eigenen Knoten, A teilt nicht - A-Personen am Knoten von A, B-Personen am Knoten von B,
//      die Knoten verbinden sich (Bifroest); alle sehen (Farbe jeder Kachel) und hoeren (Sprechring) alle.
//   b) Teilen: Knoten B geleert, A teilt - alle sechs am Knoten von A (B-Personen direkt, Token von A mit Kontingent).
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os'), http = require('http');
const E = process.env, A = E.SAGA_A, B = E.SAGA_B, CTL = E.CTL;
const UA = (E.SAGA_USERS_A || '').split(',').filter(Boolean), UB = (E.SAGA_USERS_B || '').split(',').filter(Boolean);
if (!A || !B || UA.length !== 3 || UB.length !== 3 || !E.SAGA_PASS || !CTL) { console.error('SAGA_A, SAGA_B, SAGA_USERS_A/B (je 3), SAGA_PASS, CTL setzen'); process.exit(2); }
const res = [], PERF = {}, BROWSERS = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 4000))); }
async function shot(p, name) { if (E.SHOTS) { fs.mkdirSync(E.SHOTS, { recursive: true }); await p.screenshot({ path: path.join(E.SHOTS, 'fedhorn-' + name + '.png') }); } }
const HANG = { n: 0 };
const capped = (pr, ms = 15000) => { let t; return Promise.race([Promise.resolve(pr).finally(() => clearTimeout(t)), new Promise(r => { t = setTimeout(() => { HANG.n++; r(undefined); }, ms); })]); };
async function until(fn, ms = 20000, step = 300) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await capped(fn()); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const HUES = [0, 60, 120, 180, 240, 300], FREQ = [300, 420, 540, 660, 780, 900], L = 'ABCDEF';
const ctl = (m, p) => new Promise((ok_, bad) => {
  const r = http.request(CTL + p, { method: m }, rs => { let d = ''; rs.on('data', x => { d += x; }); rs.on('end', () => { try { ok_(JSON.parse(d || '{}')); } catch (e) { ok_({}); } }); });
  r.on('error', bad); r.setTimeout(120000, () => r.destroy(new Error('timeout'))); r.end();
});

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
  const br = await PW.chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--ignore-certificate-errors', '--autoplay-policy=no-user-gesture-required', '--no-proxy-server',
           '--disable-features=WebRtcHideLocalIpsWithMdns', '--unsafely-treat-insecure-origin-as-secure=' + [A, B].map(u => new URL(u).origin).join(',')] });
  BROWSERS.push(br);
  const ctx = await br.newContext({ viewport: { width: 1000, height: 640 }, locale: 'de-DE', ignoreHTTPSErrors: true });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: base });
  const p = await ctx.newPage();
  p.__i = i; p.__base = base; p.__user = user;
  p.on('pageerror', e => console.log('  [pageerror ' + L[i] + '] ' + e.message));
  p.__cons = [];
  p.on('console', m => { const t = m.text(); if (E.DEBUG) console.log('  [console ' + L[i] + '] ' + t); if (/Content Security|CORS|blocked|refused/i.test(t) && p.__cons.length < 5) p.__cons.push(t.slice(0, 300)); });
  await p.addInitScript(FAKE, [HUES[i], FREQ[i], 320, 180, 10]);
  await p.goto(base + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(600);
  return p;
}
const RKF = p => path.join(os.tmpdir(), 'saga-fedhorn-rk-' + p.__base.replace(/\W+/g, '_') + '-' + p.__user + '.txt');
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
async function liveOk(ps) { for (const p of ps) await until(() => p.evaluate(() => !!(window.HKLive && window.HKLive.state().sse)), 15000, 200); }
async function chatByRef(p, ref) {
  return until(async () => { const r = await api(p, 'GET', '/api/chats?device=' + (await dev(p))); return r.ok && (r.r.chats || []).find(c => c.ref === ref); }, 20000, 400);
}

async function round(tag, ps, devs, gid, chats, nodeOf) {
  const hueOf = {}; devs.forEach((d, i) => { hueOf[d] = HUES[i]; });
  // neu laden (nur ein anderer #-Teil laedt nicht neu): die Seiten brauchen die aktuelle CSP (Adresse geteilter Knoten)
  for (let i = 0; i < 6; i++) { await ps[i].goto(ps[i].__base + '/chats'); await ps[i].goto(ps[i].__base + '/chats#c' + chats[i]); }
  await ps[0].waitForSelector('#ct-chat:not([hidden])'); await liveOk(ps);
  await until(() => ps[0].evaluate(() => { const v = document.getElementById('ct-call-v'); return v && !v.hidden && v.title === 'Gruppen-Videoanruf'; }), 15000);
  let t0 = Date.now();
  await ps[0].click('#ct-call-v');
  const ha = await until(async () => { const h = await horn(ps[0]); return h && h.up && h; }, 30000);
  ok(`${tag}: A startet ueber den Knoten von A (${ha && ha.node})`, ha && ha.node === nodeOf(0), ha || await horn(ps[0]));
  for (let i = 1; i < 6; i++) {
    const p = ps[i];
    const rang = await p.waitForSelector('.cl-ring', { timeout: 25000 }).then(() => true, () => false);
    t0 = Date.now();
    if (rang) await p.click('.cl-ring .cl-big');
    else { console.log('  (' + L[i] + ': kein Klingeln - tritt ueber HKCalls.join bei)'); await p.evaluate(id => window.HKCalls.join(id, 'member', true), ha.id); }
    const h = await until(async () => { const x = await horn(p); return x && x.up && x; }, 40000);
    (PERF[tag + '_beitritt_ms'] = PERF[tag + '_beitritt_ms'] || []).push(Date.now() - t0);
    ok(`${tag}: ${L[i]} (${i < 3 ? 'A' : 'B'}) verbunden mit Knoten ${h && h.node} (erwartet ${nodeOf(i)})`, h && h.node === nodeOf(i), h || { horn: await horn(p), console: p.__cons });
  }
  const all = await until(async () => {
    const hs = await Promise.all(ps.map(horn));
    return hs.every(h => h && h.up && h.known.length === 5 && h.tiles.length === 5) && hs;
  }, 40000);
  ok(`${tag}: alle 6 verbunden, Schluessel verteilt, je 5 Kacheln`, all, all || (await Promise.all(ps.map(horn))).map(h => h && { up: h.up, known: h.known.length, tiles: h.tiles.length, node: h.node }));
  t0 = Date.now();
  const seen = await until(async () => {
    const eyes = await Promise.all(ps.map(p => p.evaluate(EYES)));
    return eyes.every((e, i) => devs.filter((d, j) => j !== i).every(d => hueOk(e[d], hueOf[d]))) && eyes;
  }, 60000, 600);
  PERF[tag + '_alle_sehen_alle_ms'] = Date.now() - t0;
  ok(`${tag}: alle sehen alle 5 anderen, Farbe = Person (${PERF[tag + '_alle_sehen_alle_ms']} ms)`, !!seen,
     (seen || await Promise.all(ps.map(p => p.evaluate(EYES)))).map((e, i) => Object.fromEntries(Object.entries(e).map(([d, h]) => [L[devs.indexOf(d)], [h, hueOf[d]]]))));
  // Hoeren: nacheinander spricht eine Person von A und eine von B laut - alle anderen hoeren sie (Sprechring aus dem
  // entschluesselten Ton)
  for (const s of [1, 4]) {
    for (let i = 0; i < 6; i++) await ps[i].evaluate(v => window.__hornGain(v), i === s ? 0.6 : 0.004);
    t0 = Date.now();
    const got = await until(async () => { const hs = await Promise.all(ps.map(horn)); return hs.every((h, i) => i === s || (h && h.speaking.indexOf(devs[s]) >= 0)); }, 25000, 400);
    ok(`${tag}: ${L[s]} (${s < 3 ? 'A' : 'B'}) spricht - alle anderen hoeren (Sprechring, ${Date.now() - t0} ms)`, !!got,
       (await Promise.all(ps.map(horn))).map(h => h && h.speaking.map(x => L[devs.indexOf(x)])));
  }
  for (let i = 0; i < 6; i++) await ps[i].evaluate(() => window.__hornGain(0.004));
  const st = await Promise.all(ps.map(p => capped(p.evaluate(() => window.HKCalls.hornStats()))));
  const bad = st.map(x => { const d = (x && x.drop) || {}; return (d.auth || 0) + (d.format || 0) + (d.codec || 0); });
  ok(`${tag}: keine gefaelschten oder unverschluesselten Rahmen (Frame-E2E durchgehend)`, bad.every(n => n === 0), st.map(x => x && x.drop));
  await shot(ps[0], tag + '-a'); await shot(ps[3], tag + '-b');
  const info = await ctl('GET', '/state');
  PERF[tag + '_knoten'] = info;
  return ha.id;
}

async function hangupAll(ps, id) {
  for (const p of ps.slice().reverse()) {
    await p.evaluate(() => { const b = document.querySelector('.cl-ctl .cl-red'); if (b) b.click(); }).catch(() => {});
    await p.waitForTimeout(300);
  }
  return until(async () => { const r = await Promise.all(ps.map(p => p.evaluate(() => !window.HKCalls.state()))); return r.every(Boolean); }, 20000);
}

(async () => {
  try {
    const ps = [];
    for (let i = 0; i < 3; i++) ps.push(await session(i, A, UA[i]));
    for (let i = 0; i < 3; i++) ps.push(await session(3 + i, B, UB[i]));
    for (const p of ps) await chatKeys(p);
    const me = [];
    for (const p of ps) me.push(await until(async () => { const r = await api(p, 'GET', '/api/chat/v2/me'); return r.ok && !r.r.need && r.r.device_set_ok && r.r; }, 30000, 500));
    ok('Zertifikate v2 + signierte Geraete-Listen (6 Personen auf 2 Servern)', me.every(Boolean), me.map(x => !!x));
    const devs = [];
    for (const p of ps) devs.push(await dev(p));
    const g = await api(ps[0], 'POST', '/api/chats', { kind: 'free', members: UA.slice(1), handles: me.slice(3).map(x => x.handle), device: devs[0] });
    ok('A: freie Gruppe ueber zwei Server (3 + 3)', g.ok && g.r.chat.v === 2, g);
    const chats = [g.r.chat.id, null, null];
    for (let i = 1; i < 3; i++) chats[i] = (await chatByRef(ps[i], g.r.chat.ref)).id;
    for (let i = 3; i < 6; i++) {
      const c = await chatByRef(ps[i], g.r.chat.ref);
      await api(ps[i], 'POST', '/api/chats/' + c.id + '/request', { action: 'accept' });
      chats.push(c.id);
    }
    for (let i = 0; i < 6; i++) {
      const caps = await ps[i].evaluate(() => window.HKCalls.caps());
      await api(ps[i], 'PUT', '/api/calls/caps', { device: devs[i], caps });
    }
    // a) Kaskade
    const id1 = await round('kaskade', ps, devs, g.r.chat.id, chats, i => i < 3 ? 'horn-a.test' : 'horn-b.test');
    const k = PERF.kaskade_knoten || {};
    ok('kaskade: Bruecke an beiden Knoten "up" (Knoten B waehlt Knoten A)', k.a_up >= 1 && k.b_up >= 1, k);
    ok('kaskade: Ende', await hangupAll(ps, id1));
    // b) Teilen
    const sw = await ctl('POST', '/case-b');
    ok('Fallwechsel: Knoten B geleert, Knoten A teilt (Adressen per hello an B)', sw && sw.ok, sw);
    const id2 = await round('teilen', ps, devs, g.r.chat.id, chats, () => 'horn-a.test');
    const t = PERF.teilen_knoten || {};
    ok('teilen: keine Bruecke noetig, Kontingent von B am Knoten A belegt (3 Teilnehmende)', t.a_up === 0 && t.quota_peers === 3, t);
    ok('teilen: Ende', await hangupAll(ps, id2));
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e));
  } finally {
    for (const br of BROWSERS) await br.close().catch(() => {});
  }
  console.log('PERF ' + JSON.stringify(PERF));
  console.log(`\nBROWSER-FED-HORN ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
