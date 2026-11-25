// 2.5 B2: Browser telefonieren ueber die Saga-Adresse (Rahmen-Weg saga-frames.1, Vertrag S12). Laeuft aus tests/b2_frames.py
// (echte Kette nginx -> saga-media -> Knoten im Tunnel-Betrieb). Echte Saga-Oberflaeche, Kamera/Mikrofon-Attrappe (Farbe und
// Tonhoehe je Person).
// A, B: Chromium mit Rahmen-Weg (caps frames, Weg ws). F: Firefox (B2_FF = Pfad zum Firefox, sonst ausgelassen).
// G: Chromium ohne Rahmen-Weg (RTCRtpScriptTransform entfernt -> kein frames; Gegenprobe S11: Saga schaltet auf direkt).
//   SAGA_BASE=… SAGA_USERS=a,b,f,g SAGA_PASS=… HOST_RULES='MAP name 127.0.0.1' SAGA_NAME=name node tests/browser_b2_frames.js
'use strict';
const pw = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path');
const { ohneEinfuehrung } = require('./lib/erste_schritte');   // Frage "Erste Schritte" vorab beantworten (Bugs-DB 2428)
const E = process.env, BASE = E.SAGA_BASE, USERS = (E.SAGA_USERS || '').split(',').filter(Boolean);
if (!BASE || USERS.length < 4 || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USERS (4), SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 4000))); }
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn().catch(() => null); if (v) return v; await sleep(step); } return v; }
const HUES = [0, 120, 240, 60], FREQ = [300, 540, 780, 420], L = 'ABFG';

// Kamera/Mikrofon-Attrappe (Farbe und Ton je Person), wie browser_horn_web.js
const FAKE = ([hue, freq, real]) => {
  const md = navigator.mediaDevices;
  // Firefox sammelt ICE-Kandidaten der lokalen Schleife erst mit einer echten Medienfreigabe (wie im Anruf): einmal holen
  if (real) { const g = md.getUserMedia.bind(md); md.__real = g({ audio: true, video: true }).then(s => { s.getTracks().forEach(t => t.stop()); return true; }, () => false); }
  let ac = null, gain = null, aStream = null, vTrack = null;
  const video = () => {
    if (vTrack && vTrack.readyState === 'live') return vTrack.clone();
    const cv = document.createElement('canvas'); cv.width = 320; cv.height = 180;
    const g = cv.getContext('2d'); let f = 0;
    setInterval(() => { f++; g.fillStyle = `hsl(${hue} 85% 50%)`; g.fillRect(0, 0, 320, 180); g.fillStyle = '#fff'; g.fillRect((f * 7) % 160, 20, 16, 16); }, 1000 / 15);
    vTrack = cv.captureStream(15).getVideoTracks()[0]; return vTrack;
  };
  const audio = () => {
    if (!ac) { ac = new AudioContext(); const o = ac.createOscillator(); o.frequency.value = freq; gain = ac.createGain(); gain.gain.value = 0.3; const d = ac.createMediaStreamDestination(); o.connect(gain).connect(d); o.start(); aStream = d.stream; }
    return aStream.getAudioTracks()[0].clone();
  };
  window.__hornGain = v => { if (gain) gain.gain.value = v; };
  md.getUserMedia = async c => { const s = new MediaStream(); if (c && c.audio) s.addTrack(audio()); if (c && c.video) s.addTrack(video()); return s; };
};
const NO_FRAMES = () => { try { delete window.RTCRtpScriptTransform; } catch (e) { window.RTCRtpScriptTransform = undefined; } };
const EYES = () => {
  const cv = document.createElement('canvas'); cv.width = 32; cv.height = 18;
  const g = cv.getContext('2d', { willReadFrequently: true }), out = {};
  document.querySelectorAll('.cl-tile[data-dev]').forEach(t => {
    const v = t.querySelector('video'); if (!v || !v.videoWidth || !t.classList.contains('has-video')) { out[t.dataset.dev] = null; return; }
    g.drawImage(v, 0, 0, 32, 18);
    const [r, gg, b] = g.getImageData(28, 15, 1, 1).data, mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
    if (mx - mn < 30) { out[t.dataset.dev] = -1; return; }
    let h; if (mx === r) h = ((gg - b) / (mx - mn)) % 6; else if (mx === gg) h = (b - r) / (mx - mn) + 2; else h = (r - gg) / (mx - mn) + 4;
    out[t.dataset.dev] = Math.round((h * 60 + 360) % 360);
  });
  return out;
};
// Tonhoehen aller spielenden Tonplaetze (Spitze im Spektrum)
const EARS = async () => {
  const els = [...document.querySelectorAll('.cl-win audio.cl-raudio')].filter(a => a.srcObject && !a.muted);
  const ac = window.__earAc || (window.__earAc = new AudioContext());
  const out = [];
  for (const a of els) {
    const t = a.srcObject.getAudioTracks()[0]; if (!t) continue;
    const an = ac.createAnalyser(); an.fftSize = 8192; const src = ac.createMediaStreamSource(new MediaStream([t])); src.connect(an);
    await new Promise(r => setTimeout(r, 400));
    const f = new Float32Array(an.frequencyBinCount); an.getFloatFrequencyData(f);
    let bi = 1; for (let i = 2; i < f.length; i++) if (f[i] > f[bi]) bi = i;
    src.disconnect();
    if (f[bi] > -70) out.push(Math.round(bi * ac.sampleRate / an.fftSize));
  }
  return out;
};
const HANG = () => { const b = document.querySelector('.cl-win .cl-round.cl-red'); if (b) b.click(); return !!b; };
const hueOk = (a, b) => a != null && a >= 0 && Math.min(Math.abs(a - b), 360 - Math.abs(a - b)) < 25;
const hzOk = (list, f) => (list || []).some(x => Math.abs(x - f) < 25);

const ARGS = ['--use-fake-ui-for-media-stream', '--ignore-certificate-errors', '--autoplay-policy=no-user-gesture-required', '--no-proxy-server',
  '--host-resolver-rules=' + (E.HOST_RULES || '')];
async function session(i, kind) {
  let br;
  if (kind === 'ff') {
    // Firefox headless ohne Audio-Ausgabe: der AudioContext laeuft nicht (Attrappe, Mikrofon) - B2_PULSE = PulseAudio-Server
    const env = Object.assign({}, process.env); if (E.B2_PULSE) env.PULSE_SERVER = E.B2_PULSE;
    br = await pw.firefox.launch({ executablePath: E.B2_FF, headless: true, env, firefoxUserPrefs: {
      // forceResolve statt localDomains: mit localDomains gilt der Name als localhost - Firefox sammelt dann keine ICE-Kandidaten
      'network.dns.forceResolve': '127.0.0.1', 'media.navigator.permission.disabled': true, 'media.autoplay.default': 0,
      'media.navigator.streams.fake': true, 'network.proxy.type': 0 } });
  } else br = await pw.chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true, args: ARGS });
  const ctx = await br.newContext({ viewport: { width: 1100, height: 700 }, locale: 'de-DE', ignoreHTTPSErrors: true, bypassCSP: true });
  if (kind !== 'ff') await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.__br = br; p.__i = i;
  p.on('pageerror', e => console.log('  [pageerror ' + L[i] + '] ' + e.message));
  if (E.DEBUG || (E.DEBUG_F && kind === 'ff')) p.on('console', m => console.log('  [console ' + L[i] + '] ' + m.text()));
  if (kind === 'noframes') await p.addInitScript(NO_FRAMES);
  await p.addInitScript(FAKE, [HUES[i], FREQ[i], kind === 'ff']);
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', USERS[i]);
  await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(500);
  // Frage "Erste Schritte" (neue Konten) vorab beantworten - sonst liegt der Dialog ueber den Anruf-Knoepfen
  await ohneEinfuehrung(p);
  return p;
}
async function chatKeys(q) {
  await q.goto(BASE + '/chats'); await q.waitForTimeout(2500);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim();
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 90000 });
  }
  await q.waitForTimeout(800);
}
const horn = p => p.evaluate(() => window.HKCalls && window.HKCalls.horn()).catch(() => null);
const frames = p => p.evaluate(() => window.HKCalls && window.HKCalls.hornFrames()).catch(() => null);
const dev = p => p.evaluate(() => window.HKChat.state().device);
async function api(p, m, url, body) { return p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, msg: e.message })), [m, url, body]); }
const legsUp = h => h && h.up && h.path === 'ws' && h.legs.audio && h.legs.audio.state === 'connected' && h.legs.video && h.legs.video.state === 'connected';
async function answer(p, id) {
  const rang = await p.waitForSelector('.cl-ring', { timeout: 20000 }).then(() => true, () => false);
  if (rang) await p.click('.cl-ring .cl-big'); else await p.evaluate(id => window.HKCalls.join(id, 'member', true), id);
}

(async () => {
  let rc = 1;
  const ps = [];
  const ff = !!E.B2_FF;
  try {
    ps.push(await session(0, 'cr'), await session(1, 'cr'));
    ps.push(ff ? await session(2, 'ff') : null);
    ps.push(await session(3, 'noframes'));
    const act = ps.filter(Boolean);
    for (const p of act) await chatKeys(p);
    const devs = [];
    for (let i = 0; i < ps.length; i++) {
      if (!ps[i]) { devs.push(null); continue; }
      const d = await until(() => ps[i].evaluate(() => window.HKChat && window.HKChat.state() && window.HKChat.state().device), 15000);
      if (!d) console.log('  [' + L[i] + '] ohne Geraet: ' + ps[i].url() + ' ' + await ps[i].evaluate(() => typeof window.HKChat + ' ' + document.title));
      devs.push(d);
    }
    const members = USERS.filter((u, i) => i > 0 && ps[i]);
    const mk = await api(ps[0], 'POST', '/api/chats', { kind: 'free', members, device: devs[0] });
    ok('freie Gruppe', mk.ok, mk);
    const gid = mk.r.chat.id;
    for (const p of act) await p.goto(BASE + '/chats#c' + gid);
    await ps[0].waitForSelector('#ct-chat:not([hidden])');
    const caps = [];
    for (let i = 0; i < ps.length; i++) {
      if (!ps[i]) { caps.push(null); continue; }
      const c = await ps[i].evaluate(() => window.HKCalls.caps());
      caps.push(c);
      await api(ps[i], 'PUT', '/api/calls/caps', { device: devs[i], caps: c });
    }
    ok('caps: A/B (Chromium) melden frames, kein ws; G ohne Encoded Transform ohne frames' + (ff ? '; F (Firefox) frames' : ''),
       caps[0].includes('frames') && caps[1].includes('frames') && !caps[0].includes('ws') && !caps[3].includes('frames') && (!ff || caps[2].includes('frames')), caps);
    // F6 (Pruefer): WebKit meldet vorerst kein frames - Safari-UA bzw. navigator.vendor von Apple auf A (calls.js liest beides
    // bei jedem caps() neu); danach zurueck auf Chromium. Gegenprobe: A selbst meldet frames (oben).
    const wk = await ps[0].evaluate(async () => {
      const set = (ua, v) => { Object.defineProperty(navigator, 'userAgent', { get: () => ua, configurable: true }); Object.defineProperty(navigator, 'vendor', { get: () => v, configurable: true }); };
      const cr = navigator.userAgent, out = {};
      set('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15', 'Apple Computer, Inc.');
      out.safari = await window.HKCalls.caps();
      set(cr, 'Apple Computer, Inc.'); out.vendor = await window.HKCalls.caps();
      set('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15', ''); out.ua = await window.HKCalls.caps();
      set('Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/140.0 Mobile/15E148 Safari/604.1', 'Google Inc.');
      out.crios = await window.HKCalls.caps();
      delete navigator.userAgent; delete navigator.vendor;
      out.back = await window.HKCalls.caps();
      return out;
    });
    ok('F6: WebKit (Safari-UA + Apple-vendor, nur vendor, nur UA, Chrome auf iOS) meldet horn, aber kein frames; zurueck auf Chromium: frames',
       ['safari', 'vendor', 'ua', 'crios'].every(k => wk[k].includes('horn') && !wk[k].includes('frames') && !wk[k].includes('ws')) && wk.back.includes('frames'), wk);
    await until(() => ps[0].evaluate(() => { const v = document.getElementById('ct-call-v'); return v && !v.hidden; }), 15000);
    await ps[0].waitForTimeout(1500);

    // --- A startet: Rahmen-Weg ws -------------------------------------------------------------------------------------
    let t0 = Date.now();
    await ps[0].click('#ct-call-v');
    const ha = await until(async () => { const h = await horn(ps[0]); return legsUp(h) && h; }, 30000);
    ok(`A: Anruf ueber den Knoten im Tunnel-Betrieb, Weg ws (Rahmen), beide Leitungen verbunden (${Date.now() - t0} ms)`, ha && ha.url === BASE, ha || await horn(ps[0]));
    const fa = await frames(ps[0]);
    ok('A: lokale Schleife steht, zwei Rahmen-WebSockets (Ton, Bild) mit Bein-ID', fa && fa.state === 'up' && fa.legs.audio.id && fa.legs.video.id, fa);

    // --- B tritt bei ---------------------------------------------------------------------------------------------------
    t0 = Date.now();
    await answer(ps[1], ha.id);
    const hb = await until(async () => { const h = await horn(ps[1]); return legsUp(h) && h; }, 30000);
    ok(`B: beigetreten ueber ws (${Date.now() - t0} ms)`, !!hb, hb || await horn(ps[1]));
    const both = await until(async () => { const hs = await Promise.all([horn(ps[0]), horn(ps[1])]); return hs.every(h => h && h.up && h.known.length >= 1 && h.tiles.length >= 1) && hs; }, 30000);
    ok('A/B: Schluessel verteilt, Kachel des anderen', !!both, both || await Promise.all([horn(ps[0]), horn(ps[1])]));
    const seen = await until(async () => {
      const e = await Promise.all([ps[0].evaluate(EYES), ps[1].evaluate(EYES)]);
      return hueOk(e[0][devs[1]], HUES[1]) && hueOk(e[1][devs[0]], HUES[0]) && e;
    }, 40000, 500);
    ok('A sieht B, B sieht A (Farbe = Person) - Bild in beide Richtungen ueber die Rahmen-WebSockets', !!seen, seen || {
      eyes: await Promise.all([ps[0].evaluate(EYES), ps[1].evaluate(EYES)]), fr: await Promise.all([frames(ps[0]), frames(ps[1])]) });
    const heard = await until(async () => {
      const e = await Promise.all([ps[0].evaluate(EARS), ps[1].evaluate(EARS)]);
      return hzOk(e[0], FREQ[1]) && hzOk(e[1], FREQ[0]) && e;
    }, 20000, 500);
    ok('A hoert B, B hoert A (Tonhoehe = Person) - Ton ueber die Attrappen-Schleife (WebRTC-Empfang)', !!heard, heard || await Promise.all([ps[0].evaluate(EARS), ps[1].evaluate(EARS)]));
    // --- Sprechring auf dem Rahmen-Weg (pruef-pegel F2): Pegel aus Byte 3 der Ton-Rahmen (Knoten sagt ihn mit ok.level zu),
    // Absender per CSRC des Rahmens. Frueher: Pegel der Attrappen-Schleife (stiller Ton) -> lvSeen, Pegel 0, nie ein Ring
    const lvd = () => Promise.all([0, 1].map(async i => { const h = await horn(ps[i]); return h && { lvSeen: h.lvSeen, spk: h.spk && devs.indexOf(h.spk),
      speaking: h.speaking.map(x => devs.indexOf(x)), levels: Object.fromEntries(Object.entries(h.levels || {}).map(([d, v]) => [devs.indexOf(d), Math.round(v * 1000) / 1000])) }; }));
    const flv = await Promise.all([frames(ps[0]), frames(ps[1])]);
    ok('A/B: Knoten traegt den Pegel abwaerts (ok.level der Ton-Leitung)', flv.every(f => f && f.lvOn === true), flv.map(f => f && { lvOn: f.lvOn, lv: f.lv }));
    // erst beide leise (ohne Pegel stuende der Ring dauernd am Sprecherplatz des Knotens), dann abwechselnd: Ring nur beim
    // Gegenueber am Sprechenden, beim Sprechenden selbst keiner (das Gegenueber ist leise)
    const quietAll = async () => {
      for (let i = 0; i < 2; i++) await ps[i].evaluate(() => window.__hornGain(0.004));
      return until(async () => { const hs = await Promise.all([horn(ps[0]), horn(ps[1])]); return hs.every(h => h && h.lvSeen && h.speaking.length === 0) && hs; }, 8000, 300);
    };
    t0 = Date.now();
    const q0 = await quietAll();
    ok(`beide leise: kein Sprechring (${Date.now() - t0} ms)`, !!q0, await lvd());
    for (const s of [1, 0]) {
      const o = 1 - s;
      await ps[s].evaluate(() => window.__hornGain(0.6));
      t0 = Date.now();
      const got = await until(async () => {
        const h = await horn(ps[o]), hs = await horn(ps[s]);
        return h && h.lvSeen && h.speaking.length === 1 && h.speaking[0] === devs[s] && (h.levels[devs[s]] || 0) > 0.1
          && h.tiles.some(t => t.dev === devs[s] && t.speaking) && hs && hs.speaking.length === 0 && h;
      }, 12000, 300);
      ok(`${L[s]} spricht: Sprechring bei ${L[o]} an ${L[s]}, bei ${L[s]} keiner - Pegel aus Byte 3 (${Date.now() - t0} ms)`, !!got, await lvd());
      ok(`... danach wieder leise`, !!(await quietAll()), await lvd());
    }
    for (let i = 0; i < 2; i++) await ps[i].evaluate(() => window.__hornGain(0.3));
    const st = await Promise.all([0, 1].map(i => ps[i].evaluate(() => window.HKCalls.hornStats())));
    const drop = s => { const d = (s && s.drop) || {}; return (d.auth || 0) + (d.format || 0) + (d.codec || 0) + (d.nokey || 0) + (d.nofrom || 0); };
    const fw = await Promise.all([frames(ps[0]), frames(ps[1])]);
    const cnt = f => (f && f.worker && f.worker.cnt) || {};
    ok('Frame-E2E Variante W: versiegelt gesendet, im Worker geoeffnet und eingespeist, keine Verwerfungen (auth/format/codec/nokey/nofrom)',
       st.every(s => s && drop(s) <= 2) && fw.every(f => cnt(f)['tx-a'] > 20 && cnt(f)['tx-v'] > 20 && cnt(f)['inj-a'] > 20 && cnt(f)['inj-v'] > 5),
       { drop: st.map(s => s && s.drop), cnt: fw.map(cnt) });
    await sleep(3500);
    const da = await horn(ps[0]);
    ok('A: Diagnose transport ws, RTT des WebSockets als Weg-RTT', da && da.diag && da.diag.transport === 'ws' && da.net && da.net.rtt != null && da.net.rtt < 500,
       da && { diag: da.diag, net: da.net });
    // Bugs-DB 2357: Ton in der Diagnose - Empfang mit Paketen, Pegel/Energie und Samples (die Ton-Sendeprobe kam auf dem
    // Rahmen-Weg im Lauf 24.11. nicht - offen, geprueft wird hier nur der Empfang)
    const aIn = ((da && da.diag && da.diag.samples) || []).filter(x => x.kind === 'a' && x.dir === 'in' && x.pkts > 0);
    ok('A: Diagnose mit Ton-Proben (kind a: in mit pkts, smp, aen, alvl)', aIn.some(x => x.smp > 0 && x.aen > 0 && x.alvl != null),
       { aIn: aIn.slice(-3), n: ((da && da.diag && da.diag.samples) || []).length });

    // --- Wiederverbinden: beide WebSockets von A fallen (wie Netzabbruch) -> frische Token, gleiche leg_ref ---------------
    const before = (await frames(ps[0])).legs;
    t0 = Date.now();
    await ps[0].evaluate(() => window.HKCalls.hornFrames('kill'));
    const back = await until(async () => {
      const f = await frames(ps[0]);
      return f && f.legs.audio.state === 'connected' && f.legs.video.state === 'connected' && f.legs.audio.id !== before.audio.id && f.legs.video.id !== before.video.id && f;
    }, 15000, 100);
    const msBack = Date.now() - t0;
    const again = back && await until(async () => { const e = await ps[1].evaluate(EYES); return hueOk(e[devs[0]], HUES[0]) && e; }, 15000, 300);
    ok(`Wiederverbinden: neue Rahmen-Beine nach ${msBack} ms, Verbindung bleibt (kein Knotenwechsel), B sieht A wieder`, back && again
       && (await horn(ps[0])).switches.length === 0, { back, before, sw: (await horn(ps[0])).switches, log: (await horn(ps[0])).log.slice(-12) });

    // --- Firefox als drittes ws-Geraet ---------------------------------------------------------------------------------
    if (ff) {
      t0 = Date.now();
      await answer(ps[2], ha.id);
      const hf = await until(async () => { const h = await horn(ps[2]); return legsUp(h) && h; }, 40000);
      ok(`F (Firefox): beigetreten ueber ws (${Date.now() - t0} ms)`, !!hf, hf || await horn(ps[2]).then(h => h && { errs: h.errs, log: h.log, note: h.note, conns: h.conns, path: h.path }));
      const seen3 = await until(async () => {
        const e = await Promise.all([0, 1, 2].map(i => ps[i].evaluate(EYES)));
        return hueOk(e[2][devs[0]], HUES[0]) && hueOk(e[2][devs[1]], HUES[1]) && hueOk(e[0][devs[2]], HUES[2]) && hueOk(e[1][devs[2]], HUES[2]) && e;
      }, 45000, 700);
      ok('Firefox sieht A und B, A und B sehen Firefox', !!seen3, seen3 || { eyes: await Promise.all([0, 1, 2].map(i => ps[i].evaluate(EYES))), f: await frames(ps[2]) });
      const heardF = await until(async () => { const e = await ps[2].evaluate(EARS); return hzOk(e, FREQ[0]) && hzOk(e, FREQ[1]) && e; }, 20000, 700);
      const heardA = await until(async () => { const e = await ps[0].evaluate(EARS); return hzOk(e, FREQ[2]) && e; }, 20000, 700);
      ok('Firefox hoert A und B, A hoert Firefox', !!heardF && !!heardA, { heardF, heardA });
    }

    // --- G ohne Rahmen-Weg (Gegenprobe S11): Saga schaltet auf direkt ------------------------------------------------------
    if (E.SHOTS) { fs.mkdirSync(E.SHOTS, { recursive: true }); await ps[1].screenshot({ path: path.join(E.SHOTS, 'b2-anruf-b.png') }); }
    const gi = 3;
    await answer(ps[gi], ha.id);
    const room = p => p.evaluate(() => window.HKCalls.room()).catch(() => null);
    const mesh = await until(async () => {
      const r = await Promise.all(act.map(room));
      return r.every(x => x && !x.horn && x.topo === 'mesh') && r;
    }, 40000, 500);
    ok('G (ohne frames) tritt bei: Anruf direkt (topo mesh, kein Knoten)', !!mesh, mesh || (await Promise.all(act.map(room))).map(x => x && { horn: x.horn, topo: x.topo }));
    const need = act.length - 1;
    const connd = await until(async () => {
      const r = await Promise.all(act.map(room));
      return r.every(x => x && x.links.filter(l => !l.me && l.connected).length >= need) && r;
    }, 45000, 500);
    ok(`direkt: alle ${act.length} verbunden`, !!connd, connd ? null : (await Promise.all(act.map(room))).map(x => x && x.links.map(l => [l.name, l.connected])));
    const fz = await frames(ps[0]);
    ok('A: Rahmen-Weg nach dem Wechsel auf direkt geschlossen', fz === null, fz);
    await ps[0].evaluate(HANG);
    rc = res.every(Boolean) ? 0 : 1;
  } catch (e) {
    console.log('FAIL Ausnahme: ' + (e && e.stack || e));
    res.push(false);
  } finally {
    for (const p of ps) if (p) await p.__br.close().catch(() => {});
  }
  console.log(`\nBROWSER-B2-FRAMES ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(rc);
})();
