// Browser-Ende-zu-Ende Gjallarhorn P5 (voller Web-Client, static/calls.js + static/horn.js): Gruppenanruf mit 6 Personen
// ueber Knoten, Frame-E2E saga-horn/v1. Laeuft aus tests/anrufe_horn_web.py (eigene Instanz mit saga-media, eingebautem
// Knoten "localhost" und zweitem Knoten-Prozess NODE_B), geht aber auch allein:
//   SAGA_BASE=… SAGA_USERS=u1,…,u6 SAGA_PASS=… NODE_A_PID=… NODE_B_PID=… NODE_B=127.0.0.1 node tests/browser_horn_web.js
// Je Person ein eigener Chromium; Kamera, Mikrofon und Bildschirm sind Attrappen (Farbe und Ton je Person). Die sechste
// Person ist ein Handy (390 px, Touch): Last-N 4. Geprueft:
// - Beitritt: Kandidatenmessung (TLS + WSS-Ping), ICE-TCP, CSRC-Zuordnung vor dem ersten Rahmen (kein "nofrom")
// - alle sehen und hoeren alle; Handy: 4 bewegt, 1 Standbild; Sprechring; Sprecherwechsel (Sprecherplatz und
//   Sprecheransicht folgen der lauten Person); Bildschirm ueber den Knoten (alle sehen ihn gross, richtige Farbe)
// - Runensiegel: alle sehen dieselben 6 Runen, nach einem Abgang andere
// - Live-Umschalten: Knoten A leert sich (SIGUSR1) -> alle wechseln make-before-break auf Knoten B, gleiche
//   Frame-Schluessel, Tonluecke gemessen (Ziel 0); Knoten B faellt aus (SIGKILL) -> alle sofort zurueck auf A
// - Abgang mit neuem Schluessel; Gastgeberin schaltet stumm und entfernt jemanden (Teilnehmerliste)
// - Hinweis vor dem Start ohne bereiten Knoten (Mesh-Grenze)
// - Screenshots hell/dunkel bei 1280 und 390 px (SHOTS)
// HORN_PERF=1 (10 Personen in SAGA_USERS): 9 bewegte Kacheln bei A, CPU des Tabs und Speicher ueber PERF_MIN Minuten.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE, NODE_B = E.NODE_B || '127.0.0.1';
const USERS = (E.SAGA_USERS || '').split(',').filter(Boolean);
const PERFMODE = E.HORN_PERF === '1' && USERS.length >= 10;
if (!BASE || USERS.length < 6 || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USERS (6), SAGA_PASS setzen'); process.exit(2); }
const res = [], PERF = {}, SHOTS = E.SHOTS || '';
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 8000))); }
const HUES = [0, 60, 120, 180, 240, 300, 30, 90, 150, 210], FREQ = [300, 420, 540, 660, 780, 900, 360, 480, 600, 720], SCREEN_HUE = 270;
const L = 'ABCDEFGHIJ';
// Jede Abfrage hoechstens 15 s (eine haengende Seite soll den Test nicht anhalten, sondern als Fehler erscheinen)
const HANG = { n: 0 };
const capped = (pr, ms = 15000) => { let t; return Promise.race([Promise.resolve(pr).finally(() => clearTimeout(t)), new Promise(r => { t = setTimeout(() => { HANG.n++; console.log('  (Abfrage haengt > ' + ms + ' ms)'); r(undefined); }, ms); })]); };
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await capped(fn()); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const sleep = ms => new Promise(r => setTimeout(r, ms));

// Kamera/Mikrofon/Bildschirm-Attrappe: Farbe und Ton je Person; window.__hornGain(v) aendert die Lautstaerke
const FAKE = ([hue, freq, w, h, fps, shue]) => {
  window.__fake = { hue, freq };
  try { localStorage.setItem('saga-screen-hint', '0'); } catch (e) { /* egal */ }
  const md = navigator.mediaDevices;
  let ac = null, gain = null, aStream = null, vTrack = null;
  const canvas = (hh, big) => {
    const cv = document.createElement('canvas'); cv.width = big ? 960 : w; cv.height = big ? 540 : h;
    const g = cv.getContext('2d'); let f = 0;
    setInterval(() => {
      f++; g.fillStyle = `hsl(${hh} 85% 50%)`; g.fillRect(0, 0, cv.width, cv.height);
      const x = (f * 7) % (cv.width / 2);
      for (let i = 0; i < 32; i++) { const v = (Math.random() * 255) | 0; g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(10 + x + (i % 8) * (cv.width / 40), 10 + ((i / 8) | 0) * (cv.height / 22), cv.width / 40, cv.height / 22); }
    }, 1000 / fps);
    return cv.captureStream(fps).getVideoTracks()[0];
  };
  const video = () => { if (vTrack && vTrack.readyState === 'live') return vTrack.clone(); vTrack = canvas(hue, false); return vTrack; };
  const audio = () => {
    if (!ac) {
      ac = new AudioContext(); const o = ac.createOscillator(); o.frequency.value = freq; gain = ac.createGain(); gain.gain.value = 0.02;
      const d = ac.createMediaStreamDestination(); o.connect(gain).connect(d); o.start(); aStream = d.stream;
    }
    return aStream.getAudioTracks()[0].clone();
  };
  window.__hornGain = v => { if (gain) gain.gain.value = v; };
  md.getUserMedia = async c => { const s = new MediaStream(); if (c && c.audio) s.addTrack(audio()); if (c && c.video) s.addTrack(video()); return s; };
  md.getDisplayMedia = async () => new MediaStream([canvas(shue, true)]);
};
// Tonluecke beim Wechsel, auf dem Audio-Thread gemessen (unabhaengig von Rucklern des Hauptthreads): alle Tonplaetze
// laufen mit Verstaerkung 1 (nicht stumm) bzw. 0 (stumm geschaltet) in einen AudioWorklet, der je Block (128 Samples)
// den Pegel misst. Die laute Person spricht durchgehend, die anderen sind 43 dB leiser. Luecke = laengste Zeit ohne Block
// ueber der Schwelle. Die Stummschaltung folgt dem Element alle 20 ms.
const GAP_START = async () => {
  const ac = window.__gapAc || (window.__gapAc = new AudioContext());
  if (!window.__gapMod) {
    const code = `class G extends AudioWorkletProcessor {
      constructor() { super(); this.last = -1; this.max = 0; this.hits = 0; this.n = 0; this.t0 = currentTime;
        this.port.onmessage = e => { if (e.data === 'get') this.port.postMessage({ max: this.max, maxEnd: this.maxEnd, hits: this.hits, n: this.n, now: currentTime, last: this.last, t0: this.t0 }); }; }
      process(ins) {
        const x = ins[0] && ins[0][0]; this.n++;
        let e = 0; if (x) for (let i = 0; i < x.length; i++) e += x[i] * x[i];
        const rms = x ? Math.sqrt(e / x.length) : 0;
        if (rms > 0.03) { if (this.last >= 0) { const g = currentTime - this.last; if (g > this.max) { this.max = g; this.maxEnd = currentTime; } } this.last = currentTime; this.hits++; }
        return true; } }
      registerProcessor('saga-gap', G);`;
    window.__gapMod = ac.audioWorklet.addModule(URL.createObjectURL(new Blob([code], { type: 'application/javascript' })));
  }
  await window.__gapMod;
  if (ac.state === 'suspended') await ac.resume();
  const node = new AudioWorkletNode(ac, 'saga-gap');
  node.connect(ac.destination);                           // laeuft nur, wenn verbunden; Ausgabe ist still (kein Ausgang)
  const st = window.__gap = { node, ac, gains: new Map(), stop: false, log: [], sig: '' };
  const tick = () => {
    if (st.stop) return;
    // Wer spielt gerade (Verbindung, Platz, Absender, stumm)? Nur Aenderungen, mit der Uhr des AudioContext
    const sig = [...document.querySelectorAll('.cl-win audio.cl-raudio[data-conn]')].map(a => a.dataset.conn + '/' + a.dataset.slot + ':' + (a.dataset.owner || '-') + (a.muted ? 'M' : '')).join(' ');
    if (sig !== st.sig) { st.sig = sig; st.log.push([Math.round(ac.currentTime * 1000), sig]); }
    document.querySelectorAll('.cl-win audio.cl-raudio[data-conn]').forEach(a => {
      let g = st.gains.get(a);
      if (!g || g.s !== a.srcObject) {
        if (!a.srcObject) return;
        const src = ac.createMediaStreamSource(a.srcObject), gn = ac.createGain(); src.connect(gn).connect(node); g = { s: a.srcObject, gn }; st.gains.set(a, g);
      }
      g.gn.gain.value = (a.muted || a.paused || !a.isConnected) ? 0 : 1;
    });
    setTimeout(tick, 20);
  };
  tick();
};
const GAP_STOP = () => new Promise(ok => {
  const st = window.__gap; if (!st) return ok(null);
  st.stop = true;
  setTimeout(() => ok({ maxGap: -1, hits: 0, n: 0, err: 'keine Antwort vom AudioWorklet' }), 3000);
  st.node.port.onmessage = e => {
    const d = e.data, end = Math.round((d.maxEnd || 0) * 1000), gap = Math.round(d.max * 1000);
    ok({ maxGap: Math.round(Math.max(d.max, d.last >= 0 ? d.now - d.last : d.now - d.t0) * 1000), hits: d.hits, n: d.n, secs: Math.round((d.now - d.t0) * 10) / 10,
         gapAt: [end - gap, end], route: st.log.filter(x => x[0] > end - gap - 1500 && x[0] < end + 500) });
  };
  st.node.port.postMessage('get');
});
// Farbton jeder Kachel (Mitte rechts unten) und des grossen Bildschirms
const EYES = () => {
  const cv = document.createElement('canvas'); cv.width = 32; cv.height = 18;
  const g = cv.getContext('2d', { willReadFrequently: true }), out = {};
  const hue = v => {
    if (!v || !v.videoWidth) return null;
    g.drawImage(v, 0, 0, 32, 18);
    const [r, gg, b] = g.getImageData(28, 15, 1, 1).data, mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
    if (mx - mn < 30) return -1;
    let h; if (mx === r) h = ((gg - b) / (mx - mn)) % 6; else if (mx === gg) h = (b - r) / (mx - mn) + 2; else h = (r - gg) / (mx - mn) + 4;
    return Math.round((h * 60 + 360) % 360);
  };
  document.querySelectorAll('.cl-tile[data-dev]').forEach(t => { out[t.dataset.dev] = t.classList.contains('has-video') ? hue(t.querySelector('video')) : null; });
  const hs = document.querySelector('.cl-win.horn-scr video.cl-hscreen');
  out.__screen = hs ? hue(hs) : null;
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
const ARGS = o => ['--use-fake-ui-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + o, '--ignore-certificate-errors',
  '--autoplay-policy=no-user-gesture-required', '--no-proxy-server', '--disable-features=WebRtcHideLocalIpsWithMdns', '--enable-precise-memory-info'];
async function session(i, shared) {
  const origin = new URL(BASE).origin;
  const br = shared || await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true, args: ARGS(origin) });
  if (!shared) BROWSERS.push(br);
  const phone = i === 5, small = i >= 6;
  const ctx = await br.newContext(phone ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, locale: 'de-DE', ignoreHTTPSErrors: true, colorScheme: 'dark', bypassCSP: true }
    : { viewport: small ? { width: 800, height: 600 } : { width: 1280, height: 720 }, locale: 'de-DE', ignoreHTTPSErrors: true, colorScheme: 'dark', bypassCSP: true });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.__user = USERS[i]; p.__i = i; p.__br = br;
  p.on('pageerror', e => console.log('  [pageerror ' + L[i] + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + L[i] + '] ' + m.text()));
  await p.addInitScript(FAKE, [HUES[i], FREQ[i], 320, 180, small ? 10 : 15, SCREEN_HUE]);   // klein: 2 Kerne fuer 6+ Browser
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', USERS[i]);
  await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(500);
  return p;
}
const horn = p => capped(p.evaluate(() => window.HKCalls && window.HKCalls.horn()).then(x => x, () => null)).then(x => { if (x === undefined) console.log('  (haengt: ' + L[p.__i] + ')'); return x; });
const stats = p => capped(p.evaluate(() => window.HKCalls && window.HKCalls.hornStats()).then(x => x, () => null));
const dev = p => p.evaluate(() => window.HKChat.state().device);
async function api(p, m, url, body) { return p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, data: e.data, msg: e.message })), [m, url, body]); }
async function liveOk(ps) { for (const p of ps) await until(() => p.evaluate(() => !!(window.HKLive && window.HKLive.state().sse)), 15000, 200); }
async function shot(p, name, scheme) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  if (scheme) { await p.emulateMedia({ colorScheme: scheme }); await p.waitForTimeout(400); }
  const f = path.join(SHOTS, name + '.png');
  await p.screenshot({ path: f });
  (PERF.shots = PERF.shots || []).push(f);
}
const drops = st => { const d = (st && st.drop) || {}; return { auth: d.auth || 0, format: d.format || 0, codec: d.codec || 0, nofrom: d.nofrom || 0, nokey: d.nokey || 0, replay: d.replay || 0 }; };
const sum = (o, re) => Object.keys(o || {}).filter(k => re.test(k)).reduce((a, k) => a + o[k].n, 0);

// --- /proc: CPU und Speicher eines Browsers (Renderer des Tabs, GPU, gesamt) ---------------------------------------------
function procTree(root) {
  const kids = {}, info = {};
  for (const d of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(d)) continue;
    try {
      const st = fs.readFileSync('/proc/' + d + '/stat', 'utf8'), rest = st.slice(st.lastIndexOf(') ') + 2).split(' ');
      const cmd = fs.readFileSync('/proc/' + d + '/cmdline', 'utf8');
      info[d] = { ppid: rest[1], ticks: (+rest[11]) + (+rest[12]), type: (/--type=([a-z-]+)/.exec(cmd) || [])[1] || 'browser' };
      (kids[rest[1]] = kids[rest[1]] || []).push(d);
    } catch (e) { /* weg */ }
  }
  const out = [], walk = pid => { if (!info[pid]) return; out.push(Object.assign({ pid }, info[pid])); (kids[pid] || []).forEach(walk); };
  walk(String(root));
  return out;
}
// Playwright-Browser haben kein process(): Haupt-Prozess = n-tes Chrome-Kind dieses Node-Prozesses (Startreihenfolge)
function brPid(br) {
  const kids = [];
  for (const d of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(d)) continue;
    try {
      const st = fs.readFileSync('/proc/' + d + '/stat', 'utf8'), rest = st.slice(st.lastIndexOf(') ') + 2).split(' ');
      if (rest[1] === String(process.pid) && /chrom/.test(fs.readFileSync('/proc/' + d + '/cmdline', 'utf8'))) kids.push({ pid: d, start: +rest[19] });
    } catch (e) { /* weg */ }
  }
  kids.sort((a, b) => a.start - b.start);
  const k = kids[BROWSERS.indexOf(br)];
  return k ? k.pid : null;
}
function rssMb(pid) { try { const m = /VmRSS:\s+(\d+)/.exec(fs.readFileSync('/proc/' + pid + '/status', 'utf8')); return m ? Math.round(+m[1] / 102.4) / 10 : 0; } catch (e) { return 0; } }
async function cpuOf(br, secs) {
  const hz = 100, root = brPid(br), a = procTree(root);
  await sleep(secs * 1000);
  const b = procTree(root), by = {};
  b.forEach(x => { const o = a.find(y => y.pid === x.pid); if (!o) return; const pct = (x.ticks - o.ticks) / hz / secs * 100; by[x.type] = Math.round(((by[x.type] || 0) + pct) * 10) / 10; });
  by.total = Math.round(Object.keys(by).reduce((s, k) => s + by[k], 0) * 10) / 10;
  return by;
}
function rendererPid(br) { const t = procTree(brPid(br)).filter(x => x.type === 'renderer'); t.sort((x, y) => y.ticks - x.ticks); return t[0] && t[0].pid; }

(async () => {
  let rc = 1;
  try {
    const N = PERFMODE ? 10 : 6, ps = [];
    for (let i = 0; i < 6; i++) ps.push(await session(i));
    if (PERFMODE) {
      const shared = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true, args: ARGS(new URL(BASE).origin) });
      BROWSERS.push(shared);
      for (let i = 6; i < N; i++) ps.push(await session(i, shared));
    }
    for (const p of ps) await chatKeys(p);
    const devs = [];
    for (const p of ps) devs.push(await dev(p));
    ok(`${N} Personen mit eigenen Chat-Geraeten`, devs.every(Boolean) && new Set(devs).size === N, devs);
    const hueOf = {}; devs.forEach((d, i) => { hueOf[d] = HUES[i]; });
    const mk = await api(ps[0], 'POST', '/api/chats', { kind: 'free', members: USERS.slice(1, N), device: devs[0] });
    ok(`freie Gruppe mit ${N} Mitgliedern (ueber der Mesh-Grenze)`, mk.ok, mk);
    const gid = mk.r.chat.id;
    for (const p of ps) await p.goto(BASE + '/chats#c' + gid);
    await ps[0].waitForSelector('#ct-chat:not([hidden])'); await liveOk(ps);
    for (let i = 0; i < N; i++) {
      const caps = await ps[i].evaluate(() => window.HKCalls.caps());
      await api(ps[i], 'PUT', '/api/calls/caps', { device: devs[i], caps });
    }
    await until(async () => ps[0].evaluate(() => { const v = document.getElementById('ct-call-v'); return v && !v.hidden && v.title === 'Gruppen-Videoanruf'; }), 10000);

    // --- Hinweis vor dem Start, wenn kein Knoten bereit ist (Antwort von /api/calls/config umgebogen) ------------------
    for (const q of [ps[0], ps[5]]) {
      await q.route('**/api/calls/config', async route => { const r = await route.fetch(); const j = await r.json(); j.horn_ready = false; await route.fulfill({ response: r, json: j }); });
      await q.goto(BASE + '/chats#c' + gid);                          // Seite kennt "kein Knoten bereit" schon beim Klick
      await until(async () => q.evaluate(() => { const v = document.getElementById('ct-call-v'); return v && !v.hidden; }), 15000);
      await q.waitForTimeout(800);
      await q.click('#ct-call-v');
      const hint = await q.waitForSelector('.cl-hint-page', { timeout: 8000 }).then(() => true, () => false);
      const txt = hint ? await q.textContent('.cl-hint-page') : '';
      const winOpen = await q.evaluate(() => !!document.querySelector('.cl-win'));
      ok(`${L[q.__i]}: kein Knoten bereit -> Hinweis vor dem Start mit der Mesh-Grenze (noch kein Anruffenster)`, hint && !winOpen && /höchstens 4 Geräte mit Video/.test(txt), { txt, winOpen });
      await shot(q, 'horn-hinweis-' + (q.__i === 5 ? '390' : '1280') + '-dunkel', 'dark');
      await shot(q, 'horn-hinweis-' + (q.__i === 5 ? '390' : '1280') + '-hell', 'light');
      await q.emulateMedia({ colorScheme: 'dark' });
      await q.click('.cl-hint-page .cl-hint-no');
      await q.waitForTimeout(600);
      const after = await q.evaluate(() => ({ hint: !!document.querySelector('.cl-hint-page'), win: !!document.querySelector('.cl-win'), call: window.HKCalls.state() }));
      ok(`${L[q.__i]}: Abbrechen -> kein Anruf`, !after.hint && !after.win && !after.call, after);
      await q.unroute('**/api/calls/config');
      await q.goto(BASE + '/chats#c' + gid);
    }
    await liveOk([ps[0], ps[5]]);
    await until(async () => ps[0].evaluate(() => { const v = document.getElementById('ct-call-v'); return v && !v.hidden; }), 15000);

    // --- Anruf: A startet, alle anderen treten bei -----------------------------------------------------------------
    let t0 = Date.now();
    await ps[0].click('#ct-call-v');
    const ha = await until(async () => { const h = await horn(ps[0]); return h && h.up && h; }, 25000);
    PERF.start_klick_bis_verbunden_ms = Date.now() - t0;
    ok('A: Anruf ueber den eingebauten Knoten, Ton- und Bild-Leitung (ICE-TCP)', ha && ha.node === 'localhost' && ha.path === 'tcp'
       && ha.legs.audio.state === 'connected' && ha.legs.video.state === 'connected' && ha.legs.audio.ws && ha.legs.video.ws, ha || await horn(ps[0]));
    ok('A: Messung beim Beitritt (TLS-Aufbau und 5 WSS-Pings, <= 800 ms)', ha && ha.meas && ha.meas.localhost && ha.meas.localhost.ok
       && ha.meas.localhost.rtts.length >= 3 && ha.meas.localhost.tls != null, ha && ha.meas);
    PERF.messung_localhost = ha && ha.meas.localhost;
    ok('A: Gastgeberin', ha && ha.host === true);
    const joinMs = [];
    for (let i = 1; i < N; i++) {
      const p = ps[i];
      const rang = await p.waitForSelector('.cl-ring', { timeout: 20000 }).then(() => true, () => false);
      t0 = Date.now();
      if (rang) await p.click('.cl-ring .cl-big');
      else { console.log('  (' + L[i] + ': kein Klingeln - tritt ueber HKCalls.join bei)'); await p.evaluate(id => window.HKCalls.join(id, 'member', true), ha.id); }
      const h = await until(async () => { const x = await horn(p); return x && x.up && x; }, 30000);
      joinMs.push(Date.now() - t0);
      ok(`${L[i]}: beigetreten, beide Leitungen und Ereigniskanaele zum Knoten`, h && h.path === 'tcp' && h.node === 'localhost' && h.legs.audio.ws, h || await horn(p));
    }
    PERF.beitritt_klick_bis_verbunden_ms = joinMs;
    const all = await until(async () => {
      const hs = await Promise.all(ps.map(horn));
      return hs.every(h => h && h.up && h.known.length === N - 1 && h.tiles.length === N - 1) && hs;
    }, 30000);
    ok(`alle ${N}: verbunden, Schluessel verteilt, ${N - 1} Kacheln`, all, all || (await Promise.all(ps.map(horn))).map(h => h && { up: h.up, known: h.known.length, tiles: h.tiles.length }));
    // Sehen: Farbe jeder bewegten Kachel = Farbe der Person
    t0 = Date.now();
    const lastN = i => i === 5 ? 4 : 9;
    const seen = await until(async () => {
      const eyes = await Promise.all(ps.map(p => p.evaluate(EYES)));
      const good = eyes.every((e, i) => {
        const others = devs.filter((d, j) => j !== i), moving = others.filter(d => hueOk(e[d], hueOf[d]));
        // Leistungslauf (10 Browser auf dem Testrechner): der Knoten pausiert Plaetze nach seiner Schaetzung - dort nur
        // "nie eine falsche Farbe" und mindestens eine bewegte Kachel
        if (PERFMODE) return moving.length >= 1 && others.every(d => e[d] == null || hueOk(e[d], hueOf[d]));
        return moving.length === Math.min(others.length, lastN(i)) && others.every(d => e[d] == null || hueOk(e[d], hueOf[d]));
      });
      return good ? eyes : null;
    }, 40000, 500);
    PERF.alle_sehen_alle_ms = Date.now() - t0;
    ok(`alle sehen alle (Last-N: Desktop alle ${N - 1}, Handy 4), Farbe = Person (${PERF.alle_sehen_alle_ms} ms)`, !!seen,
       (seen || await Promise.all(ps.map(p => p.evaluate(EYES)))).map((e, i) => Object.fromEntries(Object.entries(e).map(([d, h]) => [d.slice(0, 4), [h, hueOf[d]]]))));
    const hf = await horn(ps[5]);
    const still = hf.tiles.filter(t => t.still), mov = hf.tiles.filter(t => t.video);
    ok(`Handy (Last-N 4): ${mov.length} bewegt, ${still.length} Standbild`, PERFMODE ? mov.length <= 4 && hf.tiles.length === N - 1
       : mov.length === 4 && hf.tiles.length === 5 && hf.tiles.filter(t => !t.video).length === 1, hf.tiles);
    PERF.handy_standbild = still.length;
    // CSRC-Zuordnung kam vor den Rahmen: keine verworfenen Rahmen ohne Absender
    const st0 = await Promise.all(ps.map(stats));
    const nofrom = st0.map(x => drops(x).nofrom);
    ok(`CSRC -> Geraet per Ereignis: keine Rahmen ohne Absender beim Beitritt (nofrom ${nofrom})`, nofrom.every(n => n === 0), st0.map(drops));
    ok('keine gefaelschten oder unverschluesselten Rahmen', st0.every(x => { const d = drops(x); return d.auth + d.format + d.codec === 0; }), st0.map(drops));
    // Runensiegel
    const rs = await Promise.all(ps.map(async p => { const h = await horn(p); return h.runes && h.runes.runes; }));
    ok(`Runensiegel: alle ${N} sehen dieselben 6 Runen (${rs[0]})`, rs[0] && [...rs[0]].length === 6 && rs.every(x => x === rs[0]), rs);
    // Weg-Anzeige
    const pathTxt = await ps[0].evaluate(() => { const x = document.querySelector('.cl-hpath'); return x ? { t: x.textContent, q: x.className, title: x.title } : null; });
    ok(`Weg-Anzeige mit Qualitaetspunkt (${pathTxt && pathTxt.t})`, pathTxt && pathTxt.t === 'TCP' && /cl-q-(good|mid|bad)/.test(pathTxt.q) && /localhost/.test(pathTxt.title), pathTxt);
    await shot(ps[0], 'horn-kacheln-1280-dunkel', 'dark');
    await shot(ps[0], 'horn-kacheln-1280-hell', 'light');
    await shot(ps[5], 'horn-kacheln-390-dunkel', 'dark');
    await shot(ps[5], 'horn-kacheln-390-hell', 'light');
    for (const q of [ps[0], ps[5]]) await q.emulateMedia({ colorScheme: 'dark' });

    // --- Sprecher: nacheinander spricht B, dann C laut ---------------------------------------------------------------
    const speak = async s => { for (let i = 0; i < ps.length; i++) await ps[i].evaluate(v => window.__hornGain(v), i === s ? 0.6 : 0.004); };
    await ps[0].evaluate(() => window.HKCalls.hornView('speaker'));
    for (const s of [1, 2]) {
      await speak(s);
      t0 = Date.now();
      const got = await until(async () => {
        const hs = await Promise.all(ps.map(horn));
        const big = await ps[0].evaluate(() => { const t = document.querySelector('.cl-win.horn-strip .cl-hbig .cl-tile'); return t && t.dataset.dev; });
        const ring = hs.every((h, i) => i === s || (h.speaking.indexOf(devs[s]) >= 0));
        return hs.every((h, i) => i === s || h.spk === devs[s]) && big === devs[s] && ring && { big };
      }, 15000, 300);
      ok(`${L[s]} spricht: Sprecherplatz und Sprechring bei allen, Sprecheransicht bei A gross (${Date.now() - t0} ms)`, !!got,
         (await Promise.all(ps.map(horn))).map(h => [h.spk && h.spk.slice(0, 4), h.speaking.map(x => x.slice(0, 4))]));
      if (s === 2) await shot(ps[0], 'horn-sprecher-1280-dunkel');
    }
    await shot(ps[5], 'horn-sprecher-390-dunkel', 'dark');
    await ps[0].evaluate(() => window.HKCalls.hornView('grid'));
    await ps[5].evaluate(() => window.HKCalls.hornView('grid'));

    // --- Bildschirm ueber den Knoten: A teilt --------------------------------------------------------------------------
    await ps[0].click('.cl-ctl button[aria-label="Bildschirm teilen"]');
    const scr = await until(async () => {
      const r = await Promise.all(ps.slice(1).map(async p => { const h = await horn(p); const e = await p.evaluate(EYES); return h.shared && h.shared.dev === devs[0] && h.shared.live && hueOk(e.__screen, SCREEN_HUE); }));
      return r.every(Boolean);
    }, 25000, 500);
    ok('A teilt den Bildschirm: alle anderen sehen ihn gross (richtige Farbe, ueber den Knoten)', scr,
       await Promise.all(ps.slice(1).map(async p => { const h = await horn(p); return [h.shared, (await p.evaluate(EYES)).__screen]; })));
    const bar = await ps[1].evaluate(() => { const b = document.querySelector('.cl-share'); return b && !b.hidden ? b.textContent : ''; });
    ok(`Leiste beim Empfaenger: "${bar}"`, /teilt den Bildschirm/.test(bar));
    await shot(ps[1], 'horn-bildschirm-1280-dunkel');
    await shot(ps[5], 'horn-bildschirm-390-dunkel');
    await ps[0].click('.cl-share-stop');
    const scrOff = await until(async () => (await Promise.all(ps.slice(1).map(horn))).every(h => !h.shared), 15000);
    ok('Teilen beendet: niemand zeigt mehr einen Bildschirm', scrOff);

    // --- Teilnehmerliste und Runensiegel (Bilder) --------------------------------------------------------------------
    await ps[0].click('.cl-ctl .cl-people');
    const plist = await ps[0].waitForSelector('.cl-plist', { timeout: 5000 }).then(() => true, () => false);
    const prow = await ps[0].evaluate(() => [...document.querySelectorAll('.cl-plist .cl-pl')].map(li => ({ t: li.textContent, mute: !!li.querySelector('.cl-pl-mute'), rm: !!li.querySelector('.cl-pl-rm') })));
    ok(`Teilnehmerliste bei der Gastgeberin: ${prow.length} Eintraege, Stummschalten/Entfernen fuer die anderen`, plist && prow.length === N
       && prow.filter(x => x.rm).length === N - 1 && prow.filter(x => x.mute).length >= N - 2, prow);
    await shot(ps[0], 'horn-teilnehmende-1280-dunkel');
    await ps[1].click('.cl-ctl .cl-people');
    const notHost = await ps[1].evaluate(() => document.querySelectorAll('.cl-plist .cl-pl-rm, .cl-plist .cl-pl-mute').length);
    ok('Teilnehmerliste bei anderen: ohne Gastgeber-Knoepfe', notHost === 0);
    await ps[5].click('.cl-ctl .cl-people');
    await shot(ps[5], 'horn-teilnehmende-390-dunkel');
    await ps[5].click('.cl-plist-h .cl-mini');
    await ps[1].click('.cl-plist-h .cl-mini');
    await ps[0].click('.cl-plist-h .cl-mini');
    await ps[0].click('.cl-info .cl-runes');
    const rp = await ps[0].evaluate(() => { const p = document.querySelector('.cl-rpop'); return p && p.textContent; });
    ok('Runensiegel mit Erklaerung', rp && /Vergleicht die sechs Runen/.test(rp) && rp.indexOf(rs[0]) >= 0, rp);
    await shot(ps[0], 'horn-runensiegel-1280-dunkel');
    await ps[0].click('.cl-rpop-h .cl-mini');
    await ps[5].click('.cl-info .cl-runes');
    await shot(ps[5], 'horn-runensiegel-390-dunkel');
    await ps[5].click('.cl-rpop-h .cl-mini');

    // --- Bild-im-Bild wie in 2.4 (Sprecher bzw. Bildschirm) ------------------------------------------------------------
    const pk = await ps[0].evaluate(() => window.HKCalls.state().v3.pipKind);
    const pipVis = await ps[0].isVisible('.cl-pip');
    ok(`Bild-im-Bild-Knopf im Knoten-Anruf, wo der Browser es kann (${pk || 'kein API'})`, pk ? pipVis : !pipVis);
    if (pk && pipVis) {
      await ps[0].click('.cl-pip').catch(() => {});
      const inPip = await until(async () => (await ps[0].evaluate(() => window.HKCalls.state().v3.pip)), 4000);
      PERF.pip = !!inPip;
      if (inPip) {
        const src = await ps[0].evaluate(() => { const v = document.querySelector('.cl-remote'); return !!(v && v.srcObject && v.srcObject.getVideoTracks()[0]); });
        ok('Bild-im-Bild zeigt das Sprecherbild', src);
        await ps[0].evaluate(() => window.HKCalls.pip(false));
      } else console.log('  (Bild-im-Bild im kopflosen Browser nicht moeglich)');
    }

    // --- Leistung (HORN_PERF=1): 9 bewegte Kacheln bei A, CPU des Tabs, Speicher ueber PERF_MIN Minuten ------------------
    if (PERFMODE) {
      for (let i = 0; i < N; i++) await ps[i].evaluate(v => window.__hornGain(v), 0.02);
      // Ziel 9 bewegte Kacheln; auf dem Testrechner (10 Browser, 2 Kerne) pausiert der Knoten einen Teil - gemessen wird
      // trotzdem, die Zahl der bewegten steht im Bericht
      let most = 0;
      const nine = await until(async () => { const h = await horn(ps[0]); const n = h ? h.tiles.filter(t => t.video && t.live).length : 0; most = Math.max(most, n); return n === 9 && h; }, 60000, 500);
      const hA = await horn(ps[0]);
      PERF.a_bewegt_max = most;
      ok(`A: 9 Kacheln mit Bildplatz (Last-N Desktop), bewegt hoechstens ${most} gleichzeitig`, hA && hA.tiles.length === 9 && most >= 1, hA && hA.tiles);
      if (!nine) console.log('  (nicht alle 9 bewegt: der Knoten pausiert nach seiner Schaetzung)');
      await sleep(5000);
      PERF.cpu_tab_9_kacheln_pct = await cpuOf(ps[0].__br, 30);
      console.log('CPU9 ' + JSON.stringify(PERF.cpu_tab_9_kacheln_pct));
      const mins = +(E.PERF_MIN || 10), rp0 = rendererPid(ps[0].__br), samples = [];
      for (let k = 0; k <= mins * 2; k++) {
        const m = (await capped(ps[0].evaluate(() => ({ heap: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576 * 10) / 10 : null,
                                                 dom: document.getElementsByTagName('*').length, audios: document.querySelectorAll('audio').length })), 20000)) || { heap: null, dom: null, hang: true };
        samples.push(Object.assign({ min: k / 2, rss: rssMb(rp0) }, m));
        console.log('  Speicher ' + JSON.stringify(samples[samples.length - 1]));
        if (k < mins * 2) await sleep(30000);
      }
      PERF.speicher = samples;
      const first = samples.slice(1, 5), last = samples.slice(-4), avg = (a, k) => a.reduce((s, x) => s + (x[k] || 0), 0) / a.length;
      const dRss = avg(last, 'rss') - avg(first, 'rss'), dHeap = avg(last, 'heap') - avg(first, 'heap');
      PERF.speicher_delta = { rss_mb: Math.round(dRss * 10) / 10, heap_mb: Math.round(dHeap * 10) / 10, dom: samples[samples.length - 1].dom - samples[1].dom };
      console.log('MEM ' + JSON.stringify(PERF.speicher_delta) + ' ' + JSON.stringify(samples.map(x => [x.min, x.rss, x.heap, x.dom])));
      ok(`Speicher ueber ${mins} min ohne Leck (Renderer ${PERF.speicher_delta.rss_mb} MB, JS-Heap ${PERF.speicher_delta.heap_mb} MB, DOM ${PERF.speicher_delta.dom})`,
         !samples.some(x => x.hang) && dRss < Math.max(40, avg(first, 'rss') * 0.15) && dHeap < Math.max(8, avg(first, 'heap') * 0.25) && Math.abs(PERF.speicher_delta.dom) < 50, PERF.speicher_delta);
      // die vier Zusatzpersonen gehen wieder (ihr Abgang ist hier nicht Thema)
      for (const p of ps.slice(6)) await p.click('.cl-ctl .cl-red').catch(() => {});
      ps.splice(6);
      await until(async () => (await horn(ps[0])).tiles.length === 5, 20000);
    }
    const N6 = ps.length;

    // --- Live-Umschalten: Knoten A leert sich -> alle wechseln make-before-break auf Knoten B -------------------------
    const keysBefore = (await Promise.all(ps.map(horn))).map(h => h.key_n);
    const stB = await Promise.all(ps.map(p => stats(p)));
    await speak(4);                                                       // E spricht durchgehend
    await sleep(2500);
    // Vergleich ohne Wechsel (gleiche Last): 10 s Grundmessung
    await Promise.all([0, 1, 5].map(i => ps[i].evaluate(GAP_START)));
    await sleep(10000);
    const base = await Promise.all([0, 1, 5].map(i => ps[i].evaluate(GAP_STOP)));
    PERF.tonluecke_ohne_wechsel_ms = base.map(g => g && g.maxGap);
    await Promise.all([0, 1, 5].map(i => ps[i].evaluate(GAP_START)));
    t0 = Date.now();
    process.kill(+E.NODE_A_PID, 'SIGUSR1');
    const moved = await until(async () => {
      const hs = await Promise.all(ps.map(horn));
      return hs.every(h => h && h.node === NODE_B && h.up && h.conns.length === 1) && hs;
    }, 30000, 200);
    PERF.leeren_bis_alle_auf_b_ms = Date.now() - t0;
    await sleep(1500);
    const gaps = await Promise.all([0, 1, 5].map(i => ps[i].evaluate(GAP_STOP)));
    const stG = await Promise.all([0, 1, 5].map(i => stats(ps[i])));
    gaps.forEach((g, k) => { if (g) { g.speaker = devs[4].slice(0, 8); const a = (stG[k] || {}).drop || {}, b = (stB[[0, 1, 5][k]] || {}).drop || {};
      g.audioDrops = Object.keys(a).filter(x => /^audio:/.test(x)).reduce((o, x) => { o[x] = a[x] - (b[x] || 0); return o; }, {}); } });
    const logE = ((await horn(ps[4])) || {}).log || [];
    if (gaps[0]) gaps[0].sprecherLog = logE.filter(x => /^zu|switch|ice|events/.test(x[2]));
    PERF.tonluecke_leeren_ms = gaps.map(g => g && g.maxGap);
    ok(`Leeren: alle ${N6} wechseln auf Knoten B (${PERF.leeren_bis_alle_auf_b_ms} ms), altes Bein geschlossen`, !!moved,
       (await Promise.all(ps.map(horn))).map(h => h && [h.node, h.up, h.conns.map(c => c.node), h.note]));
    const sw = moved ? moved.map(h => h.switches[h.switches.length - 1]) : [];
    PERF.leeren_wege = sw.map(x => x && x.path);
    // Knoten B meldet UDP (udp_ok): die Geraete messen per STUN und nehmen den UDP-Weg (eine Leitung mit BUNDLE)
    ok(`Wechsel make-before-break wegen draining, von localhost nach Knoten B ueber UDP (Wege ${PERF.leeren_wege})`, sw.length === N6 && sw.every(x => x && x.why === 'draining' && x.from === 'localhost' && x.to === NODE_B && x.path === 'udp'),
       { sw, log: (moved || []).map(h => h.log) });
    ok(`Frame-Schluessel bleiben gleich (n ${keysBefore} -> ${moved ? moved.map(h => h.key_n) : '?'})`, moved && moved.every((h, i) => h.key_n === keysBefore[i]));
    // Ziel 0 (Ueberlappung). Auf dem Testrechner (2 Kerne, 6 Browser, zwei Knoten) gibt es auch ohne Wechsel einzelne
    // Aussetzer; beim Wechsel kommt die Last des Aufbaus dazu. Pruefung: hoechstens 300 ms (Konzept R6) bzw. Grundmessung
    // + 40 ms; die Zuordnung (route) zeigt, ob eine Luecke aus der Umschaltung kam.
    ok(`Tonluecke beim Wechsel (A, B, Handy): ${PERF.tonluecke_leeren_ms} ms, ohne Wechsel ${PERF.tonluecke_ohne_wechsel_ms} ms (Ziel 0; <= max(300, ohne + 40) ms)`,
       gaps.every((g, k) => g && g.hits > 500 && g.maxGap <= Math.max(300, ((base[k] || {}).maxGap || 0) + 40)), { gaps, base });
    // Auf Knoten B (UDP): der Knoten verteilt nach seiner Schaetzung (GCC); reicht sie nicht, pausiert er Kacheln - die
    // zeigen dann ihr Standbild. Geprueft: jede Kachel zeigt die richtige Person (bewegt) oder ihr Standbild, nie eine
    // falsche; der Sprecher ist bei allen bewegt.
    let pausedB = null;
    const eyesB = await until(async () => {
      const e = await Promise.all(ps.map(p => p.evaluate(EYES))), hs = await Promise.all(ps.map(horn));
      const good = e.every((x, i) => devs.slice(0, N6).filter((d, j) => j !== i).every(d => {
        const t = hs[i].tiles.find(y => y.dev === d);
        return hueOk(x[d], hueOf[d]) || (t && t.still && x[d] == null);
      }) && (i === 4 || hueOk(x[devs[4]], hueOf[devs[4]])));
      pausedB = hs.map((h, i) => devs.slice(0, N6).filter((d, j) => j !== i && !hueOk(e[i][d], hueOf[d])).length);
      return good && e;
    }, 40000, 500);
    PERF.bild_wieder_da_ms = Date.now() - t0;
    PERF.udp_standbilder = pausedB;
    const eyeInfo = async () => Promise.all(ps.map(async (p, i) => {
      const e = await p.evaluate(EYES), h = await horn(p);
      return { i, node: h.node, conns: h.conns.length, eyes: Object.fromEntries(Object.entries(e).filter(([d]) => d !== '__screen').map(([d, x]) => [d.slice(0, 4), [x, hueOf[d]]])),
               tiles: h.tiles.map(t => [t.dev.slice(0, 4), t.video, t.still]), net: h.net,
               slots: ((h.legs.video || h.legs.both || {}).slots || []).filter(x => x.class !== 'audio').map(x => [x.class, x.peer && x.peer.slice(0, 4), x.layer, x.paused, x.tid]),
               est: (h.legs.video || h.legs.both || {}).est };
    }));
    ok(`auf Knoten B: alle sehen den Sprecher bewegt, die anderen bewegt oder als Standbild (pausiert je Person ${pausedB}; ${PERF.bild_wieder_da_ms} ms)`, !!eyesB, !eyesB && await eyeInfo());
    const stA = await Promise.all(ps.map(p => stats(p)));
    ok('nach dem Wechsel: keine gefaelschten Rahmen, keine ohne Absender', stA.every((x, i) => { const a = drops(x), b = drops(stB[i]); return a.auth + a.format + a.codec === 0 && a.nofrom - b.nofrom === 0; }),
       stA.map((x, i) => [drops(stB[i]), drops(x)]));
    const pB = await ps[0].evaluate(() => { const x = document.querySelector('.cl-hpath'); return x && { t: x.textContent, title: x.title }; });
    ok(`Weg-Anzeige: UDP zu Knoten B (${pB && pB.t}: ${pB && pB.title})`, pB && pB.t === 'UDP' && pB.title.indexOf(NODE_B) >= 0, pB);
    const hb = await horn(ps[0]);
    ok('UDP-Weg: eine Leitung (both), UDP-Messung beim Wechsel', hb && Object.keys(hb.legs).join() === 'both' && hb.meas[NODE_B] && (hb.meas[NODE_B].udp != null || hb.meas[NODE_B].udpMaybe),
       hb && { legs: Object.keys(hb.legs), meas: hb.meas[NODE_B] });
    await shot(ps[0], 'horn-udp-knoten-b-1280-dunkel');
    process.kill(+E.NODE_A_PID, 'SIGUSR2');                               // A nimmt wieder Beine an

    // --- Verbindung weg: Knoten B faellt aus -> alle sofort zurueck auf A -----------------------------------------------
    await sleep(16000);                                                   // A wieder bereit (Lastmeldung 5 s, Register hoechstens alle 10 s)
    await Promise.all([0, 1].map(i => ps[i].evaluate(GAP_START)));
    t0 = Date.now();
    process.kill(+E.NODE_B_PID, 'SIGKILL');
    const back = await until(async () => {
      const hs = await Promise.all(ps.map(horn));
      return hs.every(h => h && h.node === 'localhost' && h.up) && hs;
    }, 40000, 200);
    PERF.ausfall_bis_alle_auf_a_ms = Date.now() - t0;
    await sleep(1500);
    const gaps2 = await Promise.all([0, 1].map(i => ps[i].evaluate(GAP_STOP)));
    PERF.tonluecke_ausfall_ms = gaps2.map(g => g && g.maxGap);
    const sw2 = back ? back.map(h => h.switches[h.switches.length - 1]) : [];
    ok(`Knoten B faellt aus: alle ${N6} sofort zurueck auf den eingebauten Knoten (${PERF.ausfall_bis_alle_auf_a_ms} ms, Tonluecke ${PERF.tonluecke_ausfall_ms} ms)`,
       !!back && sw2.every(x => x && x.why === 'lost' && x.to === 'localhost'), (await Promise.all(ps.map(horn))).map(h => h && [h.node, h.up, h.note, h.switches.slice(-1), h.errs.slice(-4)]));
    const eyesA = await until(async () => {
      const e = await Promise.all(ps.map(p => p.evaluate(EYES)));
      return e.every((x, i) => devs.slice(0, N6).filter((d, j) => j !== i).filter(d => hueOk(x[d], hueOf[d])).length === Math.min(N6 - 1, lastN(i))) && e;
    }, 25000, 500);
    ok('wieder auf A: alle sehen alle', !!eyesA, !eyesA && await eyeInfo());
    for (const p of ps) await p.evaluate(() => window.__hornGain(0.05));

    // --- Gastgeberin schaltet D stumm ------------------------------------------------------------------------------------
    await ps[0].click('.cl-ctl .cl-people');
    let muted = false;
    const muteBtn = ps[0].locator('.cl-plist .cl-pl', { hasText: 'Dirk' }).locator('.cl-pl-mute');
    if (await muteBtn.count()) { await muteBtn.click(); muted = true; }
    const dm = await until(async () => { const r = await ps[3].evaluate(() => window.HKCalls.room()); return r && r.mic === false; }, 8000);
    const seenMute = await until(async () => { const h = await horn(ps[1]); const t = h.tiles.find(x => x.dev === devs[3]); return t && t.muted; }, 8000);
    ok('Gastgeberin schaltet D stumm: D ist stumm, die anderen sehen es an der Kachel', muted && dm && seenMute);

    // --- Abgang: E legt auf -> Neuschluessel, die anderen sehen sich weiter ---------------------------------------------
    const nBefore = (await Promise.all(ps.slice(0, 4).map(horn))).map(h => h.key_n);
    const runesBefore = (await horn(ps[0])).runes.runes;
    await ps[4].click('.cl-ctl .cl-red');
    const after = await until(async () => {
      const hs = await Promise.all([0, 1, 2, 3, 5].map(i => horn(ps[i])));
      return hs.every((h, k) => h && h.tiles.length === 4 && h.known.length === 4 && h.key_n > (k < 4 ? nBefore[k] : -1)) && hs;
    }, 15000);
    ok('E verlaesst: die anderen haben 4 Kacheln und einen neuen Schluessel', after, (await Promise.all([0, 1, 2, 3, 5].map(i => horn(ps[i])))).map(h => h && { tiles: h.tiles.length, n: h.key_n }));
    const runesAfter = await until(async () => { const r = (await horn(ps[0])).runes; return r && r.runes !== runesBefore && r.runes; }, 8000);
    ok(`Runensiegel aendert sich nach dem Abgang (${runesBefore} -> ${runesAfter})`, !!runesAfter);
    await ps[0].waitForTimeout(3500);
    // Neuer Schluessel wirkt fuer jeden Absender: jedes der fuenf Geraete ist bei mindestens einer anderen Person bewegt in
    // der richtigen Farbe zu sehen, jede Person sieht mindestens zwei bewegt; Kacheln ohne Bild nur als Standbild (der Knoten
    // pausiert auf dem ueberlasteten Testrechner einzelne Plaetze), nie in falscher Farbe
    const ids5 = [0, 1, 2, 3, 5];
    const eyes2 = await until(async () => {
      const e = await Promise.all(ids5.map(i => ps[i].evaluate(EYES))), hs = await Promise.all(ids5.map(i => horn(ps[i])));
      if (!hs.every(Boolean)) return false;
      const seen = {};
      const each = e.every((x, k) => {
        const others = ids5.filter(j => j !== ids5[k]).map(j => devs[j]);
        const mv = others.filter(d => hueOk(x[d], hueOf[d]));
        mv.forEach(d => { seen[d] = true; });
        return mv.length >= 2 && others.every(d => { const t = hs[k].tiles.find(y => y.dev === d); return hueOk(x[d], hueOf[d]) || (t && t.still && x[d] == null) || (!t && lastN(ids5[k]) < 5); });
      });
      return each && ids5.every(j => seen[devs[j]]) && e;
    }, 20000, 500);
    ok('nach dem Neuschluessel: die fuenf sehen sich weiter (jeder Absender bei anderen bewegt, keine falsche Farbe)', !!eyes2, !eyes2 && await Promise.all([0, 1, 2, 3, 5].map(async i => {
      const e = await ps[i].evaluate(EYES), h = await horn(ps[i]);
      return { i, eyes: Object.fromEntries(Object.entries(e).map(([d, x]) => [d.slice(0, 4), [x, hueOf[d]]])), tiles: h.tiles.map(t => [t.dev.slice(0, 4), t.video, t.still]), slots: (h.legs.video || {}).slots };
    })));

    // --- Gastgeberin entfernt F (Handy) ----------------------------------------------------------------------------------
    const rmBtn = () => ps[0].locator('.cl-plist .cl-pl', { hasText: 'Finn' }).locator('.cl-pl-rm');
    let sureTxt = '';
    {                                                                     // ein Klick genuegt (Sprechring zeichnet die Liste nicht neu)
      await rmBtn().click();
      sureTxt = (await until(async () => { const t = await rmBtn().textContent(); return /Wirklich/.test(t) && t; }, 2500, 100)) || await rmBtn().textContent();
    }
    ok(`Entfernen fragt nach ("${sureTxt}")`, /Wirklich/.test(sureTxt));
    await rmBtn().click();
    const gone = await until(async () => {
      const st = await ps[5].evaluate(() => ({ call: window.HKCalls.state(), txt: (document.querySelector('.cl-status') || {}).textContent || '' }));
      return !st.call && /entfernt/.test(st.txt) && st;
    }, 15000);
    ok(`F wurde entfernt: Hinweis bei F ("${gone && gone.txt}")`, !!gone);
    if (gone) await shot(ps[5], 'horn-entfernt-390-dunkel');
    const rest = await until(async () => (await Promise.all([0, 1, 2, 3].map(i => horn(ps[i])))).every(h => h.tiles.length === 3), 15000);
    ok('die anderen: 3 Kacheln', rest);
    const rj = await api(ps[5], 'POST', '/api/calls/' + ha.id + '/join', { device: devs[5], role: 'member', caps: await ps[5].evaluate(() => window.HKCalls.caps()) });
    ok(`F kann nicht wieder beitreten (${rj.status}, ${rj.data && rj.data.join_block})`, !rj.ok && rj.status === 409 && rj.data.join_block === 'removed', rj);

    for (const p of ps.slice(0, 4)) await p.click('.cl-ctl .cl-red').catch(() => {});
    await ps[0].waitForTimeout(1500);
    rc = res.every(Boolean) ? 0 : 1;
  } catch (e) {
    console.log('FAIL Ausnahme: ' + (e && e.stack || e));
    rc = 1;
  } finally {
    for (const b of BROWSERS) await b.close().catch(() => {});
  }
  console.log('PERF ' + JSON.stringify(PERF));
  console.log(`\nBROWSER-HORN-WEB ${res.filter(Boolean).length}/${res.length}` + (rc === 0 ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(rc);
})();
