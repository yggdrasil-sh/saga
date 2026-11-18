// Messreihe Anruf-Rang 3-5 (Saga 2.5, Stufe 1): ein Lauf eines Falls aus tests/anrufe_rang345.py (dort die Faelle, die
// Instanz, Leitungen und die Auswertung). Allein:
//   R345_SPEC='{"id":"r3-frei","rep":1,"kind":"dm","n":2,"secs":45,"users":["u1","u2",...],"pass":"…","base":"http://127.0.0.1:8881",
//               "prof":"/tmp/r345/prof","out":"/tmp/r345/res/r3-frei-1.json","eng":{"i":0,"exe":"/tmp/r345/chrome-r345e1.sh","ctl":"http://127.0.0.1:18891"},
//               "event":{"type":"flap|handover|share|full","at":15,"ms":3000,"who":3}}' node tests/browser_rang345.js
// Je Person ein eigener Chromium mit festem Profil (Chat-Schluessel bleiben ueber Laeufe), Person spec.eng.i ueber die
// Leitung (Namensraum, exe). Kamera = Canvas mit Farbe je Person, Rauschen und Zeitcode (16 Bit ms + 4 Bit Pruefsumme),
// Mikrofon = Dauerton. Gemessen je Empfaenger: Aufbau (Klick bzw. Annehmen bis alle Bilder mit Zeitcode / alle Tonspuren),
// Bild-Latenz (Zeitcode, gleiche Uhr), fps, freezeCount/-Dauer, Wechsel der Bildhoehe (Schicht), verdeckte Tonsamples,
// Tonluecken (0,5-s-Fenster mit > 50 % verdeckt), Weg (Knoten/Mesh), Rueckfaelle, CPU je Browser (/proc).
// Ereignisse: flap (Netzwechsel der Leitung, Erholung bis alle Bilder wieder frisch), handover (zweites Geraet der Person
// "who" uebernimmt per "Hierher übernehmen"), share (zweites Geraet von "who" kommt als Begleitgeraet und teilt den
// Bildschirm), full (Gruppe mit 5 Mitgliedern, 4 im Anruf: 5. Person nimmt an, zweites Geraet von "who" will dazu,
// Gastgeberin oeffnet "Person hinzufügen" - Texte und Bilder der Web-Anzeige).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), http = require('http');
const S = JSON.parse(process.env.R345_SPEC || '{}');
if (!S.base || !S.users || !S.out) { console.error('R345_SPEC fehlt'); process.exit(2); }
const BASE = S.base, L = 'ABCDE';
const HUES = [0, 120, 220, 50, 300], FREQ = [330, 440, 550, 660, 770], SCREEN_HUE = 270;
const MED = S.media || {}, W = +(MED.w || process.env.R345_W || 480), H = +(MED.h || process.env.R345_H || 270), FPS = +(MED.fps || process.env.R345_FPS || 20);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString().slice(11, 23), ...a);
const capped = (pr, ms = 15000) => { let t; return Promise.race([Promise.resolve(pr).finally(() => clearTimeout(t)), new Promise(r => { t = setTimeout(() => r(undefined), ms); })]); };
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await capped(fn()).catch(() => null); if (v) return v; await sleep(step); } return v; }
const getJ = url => new Promise(ok => { http.get(url, r => { let b = ''; r.on('data', d => { b += d; }); r.on('end', () => { try { ok(JSON.parse(b)); } catch (e) { ok(b); } }); }).on('error', () => ok(null)); });

// --- im Browser: Attrappen, Zeitcode, Messschleife -------------------------------------------------------------------
const INIT = ([hue, freq, w, h, fps, shue]) => {
  try { localStorage.setItem('saga-screen-hint', '0'); } catch (e) { /* egal */ }
  const P = window.RTCPeerConnection, pcs = window.__pcs = [];
  const Wr = function (...a) { const pc = new P(...a); pcs.push(pc); return pc; };
  Wr.prototype = P.prototype; Object.setPrototypeOf(Wr, P); window.RTCPeerConnection = Wr;
  const TC = {
    cols: 10, draw(g, cw, ch, ms) {
      const v = Math.floor(ms) & 0xffff, chk = ((v & 15) + ((v >> 4) & 15) + ((v >> 8) & 15) + ((v >> 12) & 15)) & 15, bits = v | (chk << 16);
      const bw = cw / this.cols, bh = Math.round(ch / 6);
      for (let i = 0; i < 20; i++) { g.fillStyle = (bits >> i) & 1 ? '#fff' : '#000'; g.fillRect((i % 10) * bw, ((i / 10) | 0) * bh, bw, bh); }
    },
  };
  window.__TC = TC;
  const canvas = (hh, big) => {
    const cv = document.createElement('canvas'); cv.width = big ? 960 : w; cv.height = big ? 540 : h;
    const g = cv.getContext('2d'); let f = 0;
    setInterval(() => {
      f++; g.fillStyle = `hsl(${hh} 85% 50%)`; g.fillRect(0, 0, cv.width, cv.height);
      const x = (f * 5) % (cv.width / 2);
      for (let i = 0; i < 24; i++) { const v = (Math.random() * 255) | 0; g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(20 + x + (i % 6) * (cv.width / 24), cv.height * 0.45 + ((i / 6) | 0) * (cv.height / 14), cv.width / 24, cv.height / 14); }
      TC.draw(g, cv.width, cv.height, Date.now());
    }, 1000 / fps);
    return cv.captureStream(fps).getVideoTracks()[0];
  };
  let ac = null, aStream = null, vTrack = null;
  const video = () => { if (vTrack && vTrack.readyState === 'live') return vTrack.clone(); vTrack = canvas(hue, false); return vTrack; };
  const audio = () => {
    if (!ac) { ac = new AudioContext(); const o = ac.createOscillator(); o.frequency.value = freq; const gn = ac.createGain(); gn.gain.value = 0.05; const d = ac.createMediaStreamDestination(); o.connect(gn).connect(d); o.start(); aStream = d.stream; }
    return aStream.getAudioTracks()[0].clone();
  };
  const md = navigator.mediaDevices;
  md.getUserMedia = async c => { const s = new MediaStream(); if (c && c.audio) s.addTrack(audio()); if (c && c.video) s.addTrack(video()); return s; };
  md.getDisplayMedia = async () => new MediaStream([canvas(shue, true)]);
  // Lesen: Zeitcode + Farbton (unten rechts) eines Videos
  const rc = document.createElement('canvas'); rc.width = 80; rc.height = 45;
  let rg = null;
  const read = el => {
    rg = rg || rc.getContext('2d', { willReadFrequently: true });
    rg.drawImage(el, 0, 0, 80, 45);
    const d = rg.getImageData(0, 0, 80, 45).data, bw = 8, bh = 45 / 6;
    let bits = 0;
    for (let i = 0; i < 20; i++) { const x = Math.floor((i % 10) * bw + bw / 2), y = Math.floor(((i / 10) | 0) * bh + bh / 2), o = (y * 80 + x) * 4; if (d[o] + d[o + 1] + d[o + 2] > 384) bits |= 1 << i; }
    const v = bits & 0xffff, chk = (bits >> 16) & 15, ok = (((v & 15) + ((v >> 4) & 15) + ((v >> 8) & 15) + ((v >> 12) & 15)) & 15) === chk;
    const o = (40 * 80 + 76) * 4, r = d[o], g = d[o + 1], b = d[o + 2], mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    let hu = -1;
    if (mx - mn >= 30) { let x; if (mx === r) x = ((g - b) / (mx - mn)) % 6; else if (mx === g) x = (b - r) / (mx - mn) + 2; else x = (r - g) / (mx - mn) + 4; hu = Math.round((x * 60 + 360) % 360); }
    let lag = null;
    if (ok) { lag = (Date.now() & 0xffff) - v; if (lag < 0) lag += 0x10000; }
    return { lag, hue: hu };
  };
  const remoteIds = () => { const s = new Set(); pcs.forEach(pc => { if (pc.connectionState !== 'closed') pc.getReceivers().forEach(r => { if (r.track) s.add(r.track.id); }); }); return s; };
  const vids = () => { const ids = remoteIds(); return [...document.querySelectorAll('video')].filter(v => v.srcObject && v.videoWidth && v.srcObject.getVideoTracks().some(t => ids.has(t.id) && t.readyState === 'live')); };
  const stats = async () => {
    const out = { v: {}, a: {}, cp: [] };
    await Promise.all(pcs.map(async (pc, i) => {
      if (pc.connectionState === 'closed') return;
      let r; try { r = await pc.getStats(); } catch (e) { return; }
      const lc = {}, rcd = {};
      r.forEach(x => {
        if (x.type === 'inbound-rtp' && x.kind === 'video') out.v[i + ':' + x.ssrc] = [x.packetsReceived || 0, x.framesDecoded || 0, x.freezeCount || 0, x.totalFreezesDuration || 0, x.frameHeight || 0, x.packetsLost || 0];
        if (x.type === 'inbound-rtp' && x.kind === 'audio') out.a[i + ':' + x.ssrc] = [x.packetsReceived || 0, x.totalSamplesReceived || 0, (x.concealedSamples || 0) - (x.silentConcealedSamples || 0), x.concealmentEvents || 0, x.packetsLost || 0];
        if (x.type === 'local-candidate') lc[x.id] = x.candidateType + '/' + x.protocol;
        if (x.type === 'remote-candidate') rcd[x.id] = x.candidateType;
      });
      r.forEach(x => { if (x.type === 'candidate-pair' && x.nominated && x.state === 'succeeded') out.cp.push([i, lc[x.localCandidateId], rcd[x.remoteCandidateId], Math.round((x.currentRoundTripTime || 0) * 1000), Math.round((x.availableOutgoingBitrate || 0) / 1000)]); });
    }));
    return out;
  };
  const M = window.__m = { s: [], on: false };
  M.snap = async () => { const st = await stats(); return { t: Date.now(), vid: vids().map(read), v: st.v, a: st.a, cp: st.cp }; };
  M.quick = async () => { const st = await stats(); const vs = vids().map(read); return { t: Date.now(), nv: vs.filter(x => x.lag != null && x.lag < 3000).length, hues: vs.map(x => x.hue), na: Object.values(st.a).filter(x => x[1] > 0).length }; };
  M.start = () => { if (M.on) return; M.on = true; const tick = async () => { if (!M.on) return; try { M.s.push(await M.snap()); } catch (e) { /* weiter */ } setTimeout(tick, 500); }; tick(); };
  M.stop = () => { M.on = false; const s = M.s; M.s = []; return s; };
};

// --- Browser, Anmeldung, Schluessel -----------------------------------------------------------------------------------
const ALL = [];
function argsFor() { return ['--use-fake-ui-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + new URL(BASE).origin, '--ignore-certificate-errors', '--autoplay-policy=no-user-gesture-required', '--no-proxy-server', '--disable-features=WebRtcHideLocalIpsWithMdns', '--auto-select-desktop-capture-source=Entire screen']; }
async function person(i, tag, eng) {
  const user = S.users[i], name = L[i] + (tag || ''), dir = path.join(S.prof, user + (tag || ''));
  fs.mkdirSync(dir, { recursive: true });
  const o = { channel: process.env.PW_CHANNEL || 'chromium', headless: true, args: argsFor(), viewport: { width: 1000, height: 640 }, locale: 'de-DE', ignoreHTTPSErrors: true, bypassCSP: true, colorScheme: 'dark' };
  if (eng) { o.executablePath = eng.exe; delete o.channel; }
  const ctx = await chromium.launchPersistentContext(dir, o);
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = ctx.pages()[0] || await ctx.newPage();
  Object.assign(p, { __user: user, __i: i, __name: name, __ctx: ctx, __dir: dir, __eng: !!eng });
  p.on('pageerror', e => log('[pageerror ' + name + '] ' + e.message));
  if (process.env.DEBUG) p.on('console', m => log('[console ' + name + '] ' + m.text()));
  await p.addInitScript(INIT, [HUES[i], FREQ[i], W, H, FPS, SCREEN_HUE]);
  ALL.push(p);
  await p.goto(BASE + '/chats');
  if (/\/login/.test(p.url())) {
    await p.fill('input[name=username], input#username, input[type=text]', user);
    await p.fill('input[type=password]', S.pass);
    await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
    await p.waitForTimeout(500);
  }
  await chatKeys(p);
  return p;
}
const RKF = p => path.join(S.prof, p.__user + '.rk');
async function chatKeys(q) {
  await q.goto(BASE + '/chats'); await q.waitForTimeout(2000);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim(); fs.writeFileSync(RKF(q), rk, { mode: 0o600 });
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 90000 });
  } else if (await q.$('.ct-rk-go') && fs.existsSync(RKF(q))) {
    await q.click('.ct-rk-go'); await q.fill('.ct-rk-in', fs.readFileSync(RKF(q), 'utf8').trim());
    await q.click('.ct-rk-ok'); await q.waitForSelector('.ct-rk-ok', { state: 'detached', timeout: 90000 });
  }
  await q.waitForTimeout(500);
}
const ev = (p, fn, a) => capped(p.evaluate(fn, a).catch(e => ({ err: String(e.message || e).slice(0, 200) })));
const api = (p, m, url, body) => ev(p, ([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, data: e.data, msg: e.message })), [m, url, body]);
const dev = p => ev(p, () => window.HKChat.state().device);
async function liveOk(ps) { for (const p of ps) await until(() => ev(p, () => !!(window.HKLive && window.HKLive.state().sse)), 15000, 200); }
async function caps(p) { const c = await ev(p, () => window.HKCalls.caps()); await api(p, 'PUT', '/api/calls/caps', { device: await dev(p), caps: c }); }
const room = p => ev(p, () => window.HKCalls && window.HKCalls.room());
const hornS = p => ev(p, () => window.HKCalls && window.HKCalls.horn());
async function openList(p) {
  if (await p.$('.cl-plist:not(.cl-addp)')) return true;
  if (await p.$('.cl-ctl .cl-people')) await p.click('.cl-ctl .cl-people');
  else if (await p.$('.cl-ctl .cl-more')) { await p.click('.cl-ctl .cl-more'); await p.waitForSelector('.cl-moremenu .cl-people', { timeout: 3000 }).catch(() => {}); await p.click('.cl-moremenu .cl-people').catch(() => {}); }
  return !!(await until(() => p.$('.cl-plist:not(.cl-addp)'), 4000));
}
async function txt(p, sel) { return ev(p, s => [...document.querySelectorAll(s)].filter(x => !x.hidden && x.getClientRects().length > 0).map(x => x.textContent.trim()).join(' | '), sel); }

// --- /proc: CPU je Browser ----------------------------------------------------------------------------------------------
function procAll() {
  const info = {};
  for (const d of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(d)) continue;
    try {
      const st = fs.readFileSync('/proc/' + d + '/stat', 'utf8'), rest = st.slice(st.lastIndexOf(') ') + 2).split(' ');
      info[d] = { ppid: rest[1], ticks: (+rest[11]) + (+rest[12]), cmd: fs.readFileSync('/proc/' + d + '/cmdline', 'utf8'), comm: st.slice(st.indexOf('(') + 1, st.lastIndexOf(')')) };
    } catch (e) { /* weg */ }
  }
  return info;
}
function cpuGroups(info, pages) {
  const kids = {}; Object.keys(info).forEach(k => { (kids[info[k].ppid] = kids[info[k].ppid] || []).push(k); });
  const out = {};
  pages.forEach(p => {
    const root = Object.keys(info).find(k => info[k].cmd.includes('--user-data-dir=' + p.__dir + '\0') && !/--type=/.test(info[k].cmd));
    let t = 0; const walk = k => { t += info[k].ticks; (kids[k] || []).forEach(walk); };
    if (root) walk(root);
    out[p.__name] = t;
  });
  const node = Object.keys(info).find(k => info[k].comm === 'gjallarhorn');
  out._node = node ? info[node].ticks : 0;
  return out;
}

// --- Auswertung je Seite -----------------------------------------------------------------------------------------------
const med = a => { const v = a.filter(x => typeof x === 'number' && !isNaN(x)).sort((x, y) => x - y); return v.length ? (v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2) : null; };
const pct = (a, q) => { const v = a.filter(x => typeof x === 'number').sort((x, y) => x - y); return v.length ? v[Math.min(v.length - 1, Math.floor(q * v.length))] : null; };
function evalPage(s, expect) {
  if (!s || s.length < 3) return null;
  const a = s[0], b = s[s.length - 1], dt = (b.t - a.t) / 1000, mins = dt / 60;
  const lags = []; s.forEach(x => x.vid.forEach(v => { if (v.lag != null && v.lag < 30000) lags.push(v.lag); }));
  // je Strom (PeerConnection + SSRC) von seinem ersten bis zu seinem letzten Auftreten - auch Stroeme, die nach einem
  // Wechsel (neue Verbindung) neu beginnen; nur Stroeme mit mindestens 10 s Laufzeit und laufenden Paketen
  const life = (kind) => {
    const first = {}, last = {};
    s.forEach((x, i) => Object.keys(x[kind]).forEach(k => { if (first[k] == null) first[k] = i; last[k] = i; }));
    return Object.keys(first).map(k => ({ k, f: s[first[k]], l: s[last[k]], fi: first[k], li: last[k] }))
      .filter(o => (o.l.t - o.f.t) >= 10000 && o.l[kind][o.k][0] - o.f[kind][o.k][0] > 50);
  };
  const vs = life('v'), as = life('a');
  let vmin = 0, fz = 0, fzs = 0, lay = 0;
  const fps = vs.map(o => {
    const d = (o.l.t - o.f.t) / 1000, A0 = o.f.v[o.k], B0 = o.l.v[o.k];
    vmin += d / 60; fz += B0[2] - A0[2]; fzs += B0[3] - A0[3];
    let last = null; for (let i = o.fi; i <= o.li; i++) { const r = s[i].v[o.k]; if (r && r[4]) { if (last != null && r[4] !== last) lay++; last = r[4]; } }
    return (B0[1] - A0[1]) / d;
  });
  let tot = 0, con = 0, gaps = 0, gapS = 0, amin = 0;
  as.forEach(o => {
    const A0 = o.f.a[o.k], B0 = o.l.a[o.k]; tot += B0[1] - A0[1]; con += B0[2] - A0[2]; amin += (o.l.t - o.f.t) / 60000;
    let inGap = false;
    for (let i = o.fi + 1; i <= o.li; i++) {
      const p = s[i - 1].a[o.k], q = s[i].a[o.k];
      if (!p || !q) continue;
      const dtot = q[1] - p[1], dcon = q[2] - p[2], bad = dtot <= 0 || dcon / dtot > 0.5;
      if (bad) { gapS += (s[i].t - s[i - 1].t) / 1000; if (!inGap) gaps++; }
      inGap = bad;
    }
  });
  // Bildluecke: laengste Zeit, in der nicht alle erwarteten Bilder frisch sind (Zeitcode < 1 s)
  let vgap = 0, since = null;
  s.forEach(x => { const fresh = x.vid.filter(v => v.lag != null && v.lag < 1000).length >= expect; if (!fresh) { if (since == null) since = x.t; vgap = Math.max(vgap, x.t - since + 500); } else since = null; });
  const pathSet = new Set(); s.forEach(x => (x.cp || []).forEach(c => pathSet.add(c[1] + '>' + c[2])));
  const rtt = med(s.map(x => med((x.cp || []).map(c => c[3]))));
  return {
    secs: Math.round(dt), lag: med(lags), lag95: pct(lags, 0.95), nlag: lags.length, vstreams: vs.length, expect,
    fps: med(fps), fpsMin: fps.length ? Math.min(...fps) : null,
    freeze: vmin ? fz / vmin : null, freeze_s: vmin ? fzs / vmin : null, layer: vmin ? lay / vmin : null, vgap,
    astreams: as.length, conceal: tot ? con / tot * 100 : null, gaps: amin ? gaps / amin : null, gap_s: gapS,
    pairs: [...pathSet], rtt, mins,
  };
}
// erster Zeitpunkt nach t, ab dem alle erwarteten Bilder wieder frisch sind (Zeitcode < 1 s alt)
function recovery(s, t, expect) {
  for (const x of s) { if (x.t < t) continue; if (x.vid.filter(v => v.lag != null && v.lag < 1000).length >= expect) return x.t - t; }
  return null;
}

// --- Ablauf ----------------------------------------------------------------------------------------------------------
(async () => {
  const R = { id: S.id, rep: S.rep, t: new Date().toISOString(), spec: Object.assign({}, S, { pass: undefined }), pages: {}, notes: [], texts: {}, shots: [] };
  let rc = 1;
  const t00 = Date.now();
  try {
    const n = S.n, full = S.event && S.event.type === 'full';
    const ps = [];
    for (let i = 0; i < n; i++) ps.push(await person(i, '', S.eng && S.eng.i === i ? S.eng : null));
    for (const p of ps) await caps(p);
    const devs = []; for (const p of ps) devs.push(await dev(p));
    R.notes.push('Geraete ' + devs.map(d => d && d.slice(0, 6)).join(','));
    log('Personen bereit', ((Date.now() - t00) / 1000).toFixed(1), 's');
    let extra = null;                                  // 5. Person (Fall full) - Mitglied, nimmt erst spaeter an
    if (full) { extra = await person(4, '', null); await caps(extra); }
    const A = ps[0];
    // Chat
    if (S.kind === 'dm') {
      await A.goto(BASE + '/chats#u' + encodeURIComponent(S.users[1]));
      for (const p of ps.slice(1)) await p.goto(BASE + '/chats');
    } else {
      const mem = S.users.slice(1, n).concat(full ? [S.users[4]] : []);
      const mk = await api(A, 'POST', '/api/chats', { kind: 'free', members: mem, device: devs[0] });
      if (!mk || !mk.ok) throw new Error('Gruppe: ' + JSON.stringify(mk).slice(0, 300));
      R.gid = mk.r.chat.id;
      for (const p of ps.concat(extra ? [extra] : [])) await p.goto(BASE + '/chats#c' + R.gid);
    }
    await A.waitForSelector('#ct-chat:not([hidden])', { timeout: 20000 });
    await liveOk(ps.concat(extra ? [extra] : []));
    await until(() => ev(A, () => { const v = document.getElementById('ct-call-v'); return v && !v.hidden; }), 15000);
    await A.waitForTimeout(800);
    const cfgA = await ev(A, () => fetch('/api/calls/config', { headers: { 'X-Saga': '1' } }).then(r => r.json()));
    R.config = cfgA && { horn_dm: cfgA.horn_dm, horn_ready: cfgA.horn_ready, limits: cfgA.limits, features: (cfgA.features || []).join(',') };
    // Messschleife ueberall schon vor dem Klick (Aufbau)
    const tClick = Date.now();
    await A.click('#ct-call-v');
    const hint = await A.waitForSelector('.cl-hint-page', { timeout: 1500 }).then(() => true, () => false);
    if (hint) { R.texts.hint_start = await txt(A, '.cl-hint-page'); await A.click('.cl-hint-page .cl-hint-go'); }
    R.texts.start_toast = await until(() => txt(A, '.toast'), full ? 2500 : 300, 100) || '';
    const tAcc = {}; tAcc[A.__name] = tClick;
    await Promise.all(ps.slice(1).map(async p => {
      const r = await p.waitForSelector('.cl-ring', { timeout: 30000 }).then(() => true, () => false);
      if (!r) { R.notes.push(p.__name + ': kein Klingeln'); return; }
      await p.waitForTimeout(300 + 400 * p.__i);
      tAcc[p.__name] = Date.now();
      await p.click('.cl-ring .cl-big').catch(e => R.notes.push(p.__name + ': ' + e.message));
    }));
    // Aufbau: je Seite bis alle n-1 Bilder mit Zeitcode bzw. alle n-1 Tonspuren
    const setup = {};
    await Promise.all(ps.map(async p => {
      const o = setup[p.__name] = { v: null, a: null, v1: null, a1: null };
      if (p === A) await until(() => Object.keys(tAcc).length >= n, 30000, 100);
      const end = Date.now() + 45000;
      while (Date.now() < end && (o.v == null || o.a == null)) {
        const q = await ev(p, () => window.__m.quick());
        if (q && !q.err) {
          // Anrufende: ab der letzten Annahme (sonst zaehlte das Warten aufs Annehmen mit), sonst ab dem eigenen Annehmen
          const t0 = p === A ? Math.max(...Object.values(tAcc)) : (tAcc[p.__name] || tClick);
          if (o.v1 == null && q.nv >= 1) o.v1 = q.t - t0;
          if (o.a1 == null && q.na >= 1) o.a1 = q.t - t0;
          if (o.v == null && q.nv >= n - 1) o.v = q.t - t0;
          if (o.a == null && q.na >= n - 1) o.a = q.t - t0;
        }
        await sleep(200);
      }
    }));
    R.setup = setup;
    log('Aufbau', JSON.stringify(setup));
    await sleep(1500);
    // Dauerphase
    for (const p of ps) await ev(p, () => window.__m.start());
    const tS = Date.now();
    const cpu0 = cpuGroups(procAll(), ps);
    if (S.eng) R.shaper0 = await getJ(S.eng.ctl + '/stats');   // Zaehler laufen ueber Laeufe weiter - Differenz bilden
    const evt = S.event; let p2 = null; const evInfo = {};
    if (evt) {
      await sleep(evt.at * 1000);
      evInfo.t = Date.now();
      if (evt.type === 'flap' && S.eng) {
        await getJ(S.eng.ctl + '/flap?ms=' + (evt.ms || 3000));
        evInfo.end = evInfo.t + (evt.ms || 3000);
        await ev(ps[S.eng.i], () => window.dispatchEvent(new Event('online')));   // wie ein Netzwechsel am Geraet
      } else if (evt.type === 'handover') {
        const who = ps[evt.who];
        p2 = await person(evt.who, '#2', null); await caps(p2);
        await p2.goto(BASE + '/chats'); await liveOk([p2]);
        const bar = await until(() => txt(p2, '.cl-run'), 20000);
        R.texts.run_bar = bar;
        await ev(p2, () => window.__m.start());
        evInfo.click = Date.now();
        await p2.click('.cl-run .cl-run-go').catch(e => R.notes.push('handover: ' + e.message));
        const got = await until(async () => { const q = await ev(p2, () => window.__m.quick()); return q && q.nv >= n - 1 && q.na >= n - 1 && q; }, 30000, 200);
        evInfo.ready_ms = got ? got.t - evInfo.click : null;
        evInfo.old_done = await until(async () => { const r = await room(who); return (!r || r.err) ? 'zu' : null; }, 15000);
        evInfo.end = evInfo.click;
      } else if (evt.type === 'share') {
        p2 = await person(evt.who, '#2', null); await caps(p2);
        await p2.goto(BASE + '/chats'); await liveOk([p2]);
        R.texts.run_bar = await until(() => txt(p2, '.cl-run'), 20000);
        evInfo.click = Date.now();
        await p2.click('.cl-run .cl-pill:not(.cl-run-go)').catch(e => R.notes.push('companion: ' + e.message));   // "Hinzufügen"
        await until(() => ev(p2, () => { const r = window.HKCalls.room(); return r && r.inRoom; }), 20000);
        evInfo.joined_ms = Date.now() - evInfo.click;
        // erst, wenn die Verbindung steht (vorher tut der Knopf nichts - toggleScreen ohne horn.cur bzw. ohne Verbindung)
        evInfo.up_ms = await until(() => ev(p2, () => { const h = window.HKCalls.horn(), r = window.HKCalls.room(); return (h ? h.up : r && r.links.length && r.links.every(l => l.connected)) && true; }), 20000, 100) ? Date.now() - evInfo.click : null;
        const sb = await p2.waitForSelector('.cl-ctl .cl-round[aria-label="Bildschirm teilen"]', { timeout: 15000 }).then(() => true, () => false);
        evInfo.tShare = Date.now();
        for (let k = 0; sb && k < 3; k++) {
          await p2.click('.cl-ctl .cl-round[aria-label="Bildschirm teilen"]').catch(() => {});
          if (await p2.waitForSelector('.cl-hint .cl-hint-go', { timeout: 1000 }).then(() => true, () => false)) { R.texts.share_hint = await txt(p2, '.cl-hint'); await p2.click('.cl-hint .cl-hint-go'); }
          if (await until(() => ev(p2, () => { const x = window.HKCalls.screen(); return x && x.sharing; }), 2500, 100)) break;
          evInfo.share_retry = k + 1;
        }
        evInfo.share_btn = sb;
        const seen = await Promise.all(ps.map(async p => { const t = await until(async () => { const q = await ev(p, () => window.__m.quick()); return q && q.hues && q.hues.some(h => h >= 0 && Math.abs(h - 270) < 25) && Date.now(); }, 20000, 200); return [p.__name, t ? t - evInfo.tShare : null]; }));
        evInfo.screen_ms = Object.fromEntries(seen);
        evInfo.p2 = await ev(p2, () => { const x = window.HKCalls.screen(), h = window.HKCalls.horn(); return { sharing: x && x.sharing, mode: x && x.mode, horn_screen: h && h.screen }; });
        evInfo.a_seen = await ev(A, () => { const h = window.HKCalls.horn(); return { shared: h && h.shared, tiles: h && h.tiles.length, hues: [...document.querySelectorAll('video')].length }; });
        R.shots.push(await shot(A, 'teilen-a'));
        const c = await api(A, 'GET', '/api/calls/' + (await room(A)).id);
        evInfo.seats = c && c.ok ? { legs: (c.r.call.legs || []).length, limit: c.r.call.limit, joinable: c.r.call.joinable, block: c.r.call.join_block } : c;
        evInfo.end = evInfo.tShare;
      } else if (evt.type === 'full') {
        // (1) 5. Person: lehnt das Klingeln ab (Stand "hat abgelehnt" fuer Erneut klingeln), sieht dann die Leiste mit dem
        // Grund und versucht trotzdem beizutreten (bzw. ohne Klingeln gleich ueber die Leiste)
        const rang = await extra.$('.cl-ring');
        R.texts.extra_ring = rang ? await txt(extra, '.cl-ring') : '';
        if (rang) await extra.click('.cl-ring .cl-red').catch(() => {});
        R.texts.extra_bar = await until(() => txt(extra, '.cl-run'), 10000) || '';
        R.texts.extra_bar_join = !!(await extra.$('.cl-run .cl-run-go'));
        R.shots.push(await shot(extra, 'voll-5-leiste'));
        const go = await extra.$('.cl-run .cl-run-go');
        if (go) { await go.click(); await sleep(4000); R.texts.extra_win = await txt(extra, '.cl-win .cl-st, .cl-win [role=status], .toast'); R.shots.push(await shot(extra, 'voll-5-person')); }
        // Beitritt direkt ueber die Schnittstelle (wie eine App): Text des Servers
        const ec = await api(extra, 'POST', '/api/calls/' + (await room(A)).id + '/join', { device: await dev(extra), role: 'member', caps: await ev(extra, () => window.HKCalls.caps()), dev_class: 'desktop' });
        R.texts.join_api = ec && !ec.ok ? { status: ec.status, block: ec.data && ec.data.join_block, error: ec.data && ec.data.error } : ec;
        if (ec && ec.ok) await api(extra, 'POST', '/api/calls/' + (await room(A)).id + '/leave', { device: await dev(extra) });
        // (2) zweites Geraet einer Person im Anruf (Teilen) sieht die Leiste
        p2 = await person(evt.who, '#2', null); await caps(p2);
        await p2.goto(BASE + '/chats#c' + R.gid); await liveOk([p2]);
        R.texts.second_bar = await until(() => txt(p2, '.cl-run'), 15000);
        R.shots.push(await shot(p2, 'voll-zweitgeraet'));
        // (3) Gastgeberin: "Person hinzufügen"
        await openList(A); await sleep(600);
        const addB = await A.$('.cl-plist .cl-add-go');
        await until(() => ev(A, () => !!document.querySelector('.cl-away .cl-pl-ring')), 8000);
        R.texts.ring_again = await ev(A, () => [...document.querySelectorAll('.cl-away .cl-pl-ring')].map(b => ({ t: b.textContent, disabled: b.disabled, title: b.title })));
        R.shots.push(await shot(A, 'voll-liste'));
        if (addB) { await addB.click(); await sleep(1200); R.texts.add_hint = await txt(A, '.cl-add-hint'); R.shots.push(await shot(A, 'voll-hinzufuegen')); await A.keyboard.press('Escape'); }
        else R.texts.add_hint = '(kein Knopf Person hinzufügen)';
        const c = await api(A, 'GET', '/api/calls/' + (await room(A)).id);
        evInfo.seats = c && c.ok ? { legs: (c.r.call.legs || []).length, limit: c.r.call.limit } : c;
        evInfo.end = Date.now();
      }
    }
    const rest = Math.max(5, S.secs - (Date.now() - tS) / 1000);
    await sleep(rest * 1000);
    const cpu1 = cpuGroups(procAll(), ps), dtc = (Date.now() - tS) / 1000;
    R.cpu = {}; Object.keys(cpu1).forEach(k => { R.cpu[k] = Math.round((cpu1[k] - (cpu0[k] || 0)) / 100 / dtc * 1000) / 10; });
    R.loadavg = fs.readFileSync('/proc/loadavg', 'utf8').trim();
    // Ergebnisse je Seite
    const all = ps.concat(p2 && evt && evt.type === 'handover' ? [p2] : []);
    for (const p of all) {
      const s = await ev(p, () => window.__m.stop(), null) || [];
      const nm = p.__name;
      const expect = n - 1;
      const pe = Array.isArray(s) ? evalPage(s, expect) : null;
      if (pe && evInfo.end) pe.recover = recovery(s, evInfo.end, expect);
      if (pe && evt && evt.type === 'handover') {          // Bild der wechselnden Person bei den anderen: laengste Luecke
        const hue = HUES[evt.who]; let last = null, gap = 0;
        s.forEach(x => { const fresh = x.vid.some(v => v.lag != null && v.lag < 1000 && v.hue >= 0 && Math.min(Math.abs(v.hue - hue), 360 - Math.abs(v.hue - hue)) < 25); if (fresh) { if (last != null && x.t - last > gap) gap = x.t - last; last = x.t; } });
        pe.who_gap = gap;
      }
      const r = await room(p), h = S.horn ? await hornS(p) : null;
      R.pages[nm] = Object.assign({ eng: p.__eng }, pe || { err: 'keine Messwerte' }, {
        topo: r && r.topo, direct: r && r.direct, links: r && r.links ? r.links.length : null,
        horn: h && !h.err ? { path: h.path, node: h.node && String(h.node).slice(0, 8), switches: h.switches, errs: (h.errs || []).slice(-5), note: h.note, view: h.view, host: h.host,
          tiles: (h.tiles || []).map(t => (t.big ? 'B' : '') + (t.still ? 'S' : '') + (t.video ? 'v' : '-')).join(' ') } : null,
      });
    }
    R.event = evInfo;
    if (S.eng) R.shaper = await getJ(S.eng.ctl + '/stats');
    // Zusammenfassung des Laufs (Tabelle): ueber alle Empfaenger
    const pv = Object.values(R.pages).filter(x => x.secs);
    const cpuB = Object.keys(R.cpu).filter(k => !k.startsWith('_') && !k.includes('#')).map(k => R.cpu[k]);
    const sv = Object.values(setup).map(x => x.v), sa = Object.values(setup).map(x => x.a);
    const falls = Object.values(R.pages).reduce((t, x) => t + (x.direct ? 1 : 0) + (S.horn && x.topo && x.topo !== 'horn' ? 1 : 0), 0);
    const sw = Object.values(R.pages).reduce((t, x) => t + ((x.horn && Array.isArray(x.horn.switches) ? x.horn.switches.length : 0) || 0), 0);
    R.sum = {
      path: S.horn ? [...new Set(Object.values(R.pages).map(x => (x.horn && x.horn.path) || x.topo || '?'))].join('/') : 'mesh',
      setup_v: sv.some(x => x == null) ? null : Math.max(...sv), setup_a: sa.some(x => x == null) ? null : Math.max(...sa),
      lag: med(pv.map(x => x.lag)), lag_max: Math.max(...pv.map(x => x.lag95 || 0)), fps: med(pv.map(x => x.fps)), fps_min: Math.min(...pv.map(x => x.fpsMin == null ? 99 : x.fpsMin)),
      freeze: med(pv.map(x => x.freeze)), freeze_s: med(pv.map(x => x.freeze_s)), layer: med(pv.map(x => x.layer)), conceal: med(pv.map(x => x.conceal)),
      gaps: med(pv.map(x => x.gaps)), cpu: med(cpuB), cpu_max: cpuB.length ? Math.max(...cpuB) : null, node_cpu: R.cpu._node, fall: falls, sw, vgap: Math.max(...pv.map(x => x.vgap || 0)), gap_s: Math.max(...pv.map(x => x.gap_s || 0)),
      recover: evInfo.end ? (Math.max(...pv.map(x => x.recover == null ? 1e9 : x.recover)) >= 1e9 ? null : Math.max(...pv.map(x => x.recover))) : null,
      missing_setup: sv.filter(x => x == null).length,
    };
    if (S.eng) { const e = R.pages[L[S.eng.i]]; R.sum.eng = e && { lag: e.lag, fps: e.fps, freeze: e.freeze, conceal: e.conceal, gaps: e.gaps }; }
    if (evt && evt.type === 'handover') R.sum.recover = evInfo.ready_ms, R.sum.who_gap = med(Object.values(R.pages).map(x => x.who_gap).filter(x => x != null && x > 0));
    if (evt && evt.type === 'share') {                // Bildschirm bei den anderen Personen (ohne die teilende selbst)
      const o = Object.keys(evInfo.screen_ms || {}).filter(k => k !== L[evt.who]).map(k => evInfo.screen_ms[k]);
      R.sum.recover = o.some(x => x == null) ? null : Math.max(...o); R.sum.screen_missing = o.filter(x => x == null).length;
    }
    log('SUM', JSON.stringify(R.sum));
    rc = 0;
  } catch (e) {
    R.error = String(e && e.stack || e).slice(0, 2000);
    log('FEHLER', R.error);
  } finally {
    R.secs = Math.round((Date.now() - t00) / 1000);
    for (const p of ALL) { try { await capped(p.evaluate(() => { const b = document.querySelector('.cl-ctl .cl-red'); if (b) b.click(); }), 3000); } catch (e) { /* egal */ } }
    await sleep(1500);
    for (const p of ALL) { try { await capped(p.__ctx.close(), 10000); } catch (e) { /* egal */ } }
    if (rc === 0) { fs.mkdirSync(path.dirname(S.out), { recursive: true }); fs.writeFileSync(S.out + '.tmp', JSON.stringify(R)); fs.renameSync(S.out + '.tmp', S.out); }
    else fs.writeFileSync(S.out.replace(/\.json$/, '.fehler.json'), JSON.stringify(R));
    process.exit(rc);
  }
})();

async function shot(p, name) {
  const dir = path.join(path.dirname(S.out), '..', 'bilder'); fs.mkdirSync(dir, { recursive: true });
  const f = path.join(dir, S.id + '-' + S.rep + '-' + name + '.png');
  await capped(p.screenshot({ path: f }).catch(() => {}), 10000);
  return f;
}
