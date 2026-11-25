// 2263-2 Messwerkzeug, Browser-Teil (aus tools/perf/horn_last.py): 1-6 Chromium treten demselben Knoten-Anruf bei wie die
// Mess-Bots (gjallarhorn horn-bot) und messen die Qualitaet mit echtem Kodierer/Dekodierer - Messcode wie
// tests/browser_rang345.js: Kamera = Canvas mit Farbe je Person, Rauschen und Zeitcode (16 Bit ms + 4 Bit Pruefsumme),
// Mikrofon = Dauerton; je Browser alle 0,5 s getStats + Zeitcode der sichtbaren Bilder.
// Kennzahlen je Browser: Beitritt bis erstes Bild (Zeitcode) / erster Ton, Bild-Latenz p50/p95 (nur Bilder anderer
// Browser - die Bots senden keine dekodierbaren Bilder), fps Sprecher (hoechstes Bild) und Kacheln, Einfrierer/min
// (freezeCount), Tonluecken (0,5-s-Fenster mit > 50 % verdeckt) und Verdeckung %, packetsLost, Weg/Knoten, CPU je Browser.
// Ton: alle Tonplaetze zaehlen (auch Bots; sie senden gueltiges Opus = Stille, also kaum Verdeckung).
//   HG_SPEC='{"base":"https://saga.example.org:8960","users":["hl63-w1"],"pass":"…","call":"<id>","secs":30,"video":true,
//             "out":"/tmp/x.json","prof":"/tmp/prof","host_rules":"MAP saga.example.org 127.0.0.1","eng":{"i":0,"exe":"…"}}'
//   node tests/browser_horn_gruppe.js
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path');
const S = JSON.parse(process.env.HG_SPEC || '{}');
if (!S.base || !S.users || !S.call || !S.out) { console.error('HG_SPEC fehlt'); process.exit(2); }
const BASE = S.base, HUES = [0, 120, 220, 50, 300, 170], FREQ = [330, 440, 550, 660, 770, 880];
const W = 480, H = 270, FPS = 20;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString().slice(11, 23), ...a);
const capped = (pr, ms = 15000) => { let t; return Promise.race([Promise.resolve(pr).finally(() => clearTimeout(t)), new Promise(r => { t = setTimeout(() => r(undefined), ms); })]); };
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await capped(fn()).catch(() => null); if (v) return v; await sleep(step); } return v; }

// --- im Browser (wie browser_rang345.js INIT, ohne Bildschirm) -----------------------------------------------------
const INIT = ([hue, freq, w, h, fps]) => {
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
  let vTrack = null, ac = null, aStream = null;
  const video = () => {
    if (vTrack && vTrack.readyState === 'live') return vTrack.clone();
    const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
    const g = cv.getContext('2d'); let f = 0;
    setInterval(() => {
      f++; g.fillStyle = `hsl(${hue} 85% 50%)`; g.fillRect(0, 0, w, h);
      const x = (f * 5) % (w / 2);
      for (let i = 0; i < 24; i++) { const v = (Math.random() * 255) | 0; g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(20 + x + (i % 6) * (w / 24), h * 0.45 + ((i / 6) | 0) * (h / 14), w / 24, h / 14); }
      TC.draw(g, w, h, Date.now());
    }, 1000 / fps);
    vTrack = cv.captureStream(fps).getVideoTracks()[0]; return vTrack;
  };
  const audio = () => {
    if (!ac) { ac = new AudioContext(); const o = ac.createOscillator(); o.frequency.value = freq; const gn = ac.createGain(); gn.gain.value = 0.05; const d = ac.createMediaStreamDestination(); o.connect(gn).connect(d); o.start(); aStream = d.stream; }
    return aStream.getAudioTracks()[0].clone();
  };
  navigator.mediaDevices.getUserMedia = async c => { const s = new MediaStream(); if (c && c.audio) s.addTrack(audio()); if (c && c.video) s.addTrack(video()); return s; };
  const rc = document.createElement('canvas'); rc.width = 80; rc.height = 45;
  let rg = null;
  const LAST = new WeakMap();
  const read = el => {
    rg = rg || rc.getContext('2d', { willReadFrequently: true });
    rg.drawImage(el, 0, 0, 80, 45);
    const d = rg.getImageData(0, 0, 80, 45).data, bw = 8, bh = 45 / 6;
    let bits = 0;
    for (let i = 0; i < 20; i++) { const x = Math.floor((i % 10) * bw + bw / 2), y = Math.floor(((i / 10) | 0) * bh + bh / 2), o = (y * 80 + x) * 4; if (d[o] + d[o + 1] + d[o + 2] > 384) bits |= 1 << i; }
    const v = bits & 0xffff, chk = (bits >> 16) & 15, ok = (((v & 15) + ((v >> 4) & 15) + ((v >> 8) & 15) + ((v >> 12) & 15)) & 15) === chk && bits !== 0;
    // Farbton unten rechts: das eigene Bild (Schleife pcX -> pcY des Rahmen-Wegs, Vorschau) nicht mitzaehlen
    const o = (40 * 80 + 76) * 4, r = d[o], g = d[o + 1], b = d[o + 2], mx = Math.max(r, g, b), mn = Math.min(r, g, b);
    let hu = -1;
    if (mx - mn >= 30) { let x; if (mx === r) x = ((g - b) / (mx - mn)) % 6; else if (mx === g) x = (b - r) / (mx - mn) + 2; else x = (r - g) / (mx - mn) + 4; hu = Math.round((x * 60 + 360) % 360); }
    if (hu >= 0 && Math.min(Math.abs(hu - hue), 360 - Math.abs(hu - hue)) < 25) return null;
    // stehendes Bild (gleicher Zeitcode wie in der letzten Probe, z. B. Platz zeigt gerade einen Bot): keine Latenz
    if (!ok || LAST.get(el) === v) return null;
    LAST.set(el, v);
    let lag = (Date.now() & 0xffff) - v; if (lag < 0) lag += 0x10000;
    return lag;
  };
  // nur Bilder von Empfaengern (nicht die eigene Vorschau)
  const remoteIds = () => { const s = new Set(); pcs.forEach(pc => { if (pc.connectionState !== 'closed') pc.getReceivers().forEach(r => { if (r.track) s.add(r.track.id); }); }); return s; };
  const vids = () => { const ids = remoteIds(); return [...document.querySelectorAll('video')].filter(v => v.srcObject && v.videoWidth && v.srcObject.getVideoTracks().some(t => ids.has(t.id) && t.readyState === 'live')); };
  const stats = async () => {
    const out = { v: {}, a: {} };
    await Promise.all(pcs.map(async (pc, i) => {
      if (pc.connectionState === 'closed') return;
      let r; try { r = await pc.getStats(); } catch (e) { return; }
      r.forEach(x => {
        if (x.type === 'inbound-rtp' && x.kind === 'video') out.v[i + ':' + x.ssrc] = [x.packetsReceived || 0, x.framesDecoded || 0, x.freezeCount || 0, x.totalFreezesDuration || 0, x.frameHeight || 0, x.packetsLost || 0];
        if (x.type === 'inbound-rtp' && x.kind === 'audio') out.a[i + ':' + x.ssrc] = [x.packetsReceived || 0, x.totalSamplesReceived || 0, (x.concealedSamples || 0) - (x.silentConcealedSamples || 0), x.concealmentEvents || 0, x.packetsLost || 0];
      });
    }));
    return out;
  };
  const M = window.__m = { s: [], on: false };
  M.snap = async () => { const st = await stats(); return { t: Date.now(), lag: vids().map(read), v: st.v, a: st.a }; };
  M.start = () => { if (M.on) return; M.on = true; const tick = async () => { if (!M.on) return; try { M.s.push(await M.snap()); } catch (e) { /* weiter */ } setTimeout(tick, 500); }; tick(); };
  M.stop = () => { M.on = false; const s = M.s; M.s = []; return s; };
};

// --- Auswertung -----------------------------------------------------------------------------------------------------
const med = a => { const v = a.filter(x => typeof x === 'number' && !isNaN(x)).sort((x, y) => x - y); return v.length ? (v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2) : null; };
const pct = (a, q) => { const v = a.filter(x => typeof x === 'number').sort((x, y) => x - y); return v.length ? v[Math.min(v.length - 1, Math.floor(q * v.length))] : null; };
const r1 = x => (x == null ? null : Math.round(x * 10) / 10);
function evalPage(s) {
  if (!s || s.length < 3) return null;
  const a = s[0], b = s[s.length - 1], secs = (b.t - a.t) / 1000;
  const lags = []; s.forEach(x => x.lag.forEach(v => { if (v != null && v < 30000) lags.push(v); }));
  const life = kind => {
    const first = {}, last = {};
    s.forEach((x, i) => Object.keys(x[kind]).forEach(k => { if (first[k] == null) first[k] = i; last[k] = i; }));
    return Object.keys(first).map(k => ({ k, f: s[first[k]], l: s[last[k]], fi: first[k], li: last[k] })).filter(o => (o.l.t - o.f.t) >= 5000);
  };
  // Bild: nur dekodierte Stroeme (Browser); Bots senden Zufall statt VP8
  const vs = life('v').filter(o => o.l.v[o.k][1] - o.f.v[o.k][1] > 10);
  let vmin = 0, fz = 0, lostV = 0;
  const per = vs.map(o => {
    const d = (o.l.t - o.f.t) / 1000, A0 = o.f.v[o.k], B0 = o.l.v[o.k];
    vmin += d / 60; fz += B0[2] - A0[2]; lostV += B0[5] - A0[5];
    const hs = []; for (let i = o.fi; i <= o.li; i++) { const r = s[i].v[o.k]; if (r && r[4]) hs.push(r[4]); }
    return { fps: (B0[1] - A0[1]) / d, h: med(hs) || 0 };
  }).sort((x, y) => y.h - x.h || y.fps - x.fps);
  const undec = life('v').length - vs.length;
  const as = life('a').filter(o => o.l.a[o.k][0] - o.f.a[o.k][0] > 20);
  let tot = 0, con = 0, gaps = 0, amin = 0, lostA = 0;
  as.forEach(o => {
    const A0 = o.f.a[o.k], B0 = o.l.a[o.k]; tot += B0[1] - A0[1]; con += B0[2] - A0[2]; amin += (o.l.t - o.f.t) / 60000; lostA += B0[4] - A0[4];
    let inGap = false;
    for (let i = o.fi + 1; i <= o.li; i++) {
      const p = s[i - 1].a[o.k], q = s[i].a[o.k]; if (!p || !q) continue;
      // Platz pausiert (keine Samples, z. B. stiller Bot): keine Luecke; Luecke = > 50 % verdeckt
      const dt = q[1] - p[1], dc = q[2] - p[2], bad = dt > 0 && dc / dt > 0.5;
      if (bad && !inGap) gaps++;
      inGap = bad;
    }
  });
  return {
    secs: Math.round(secs), lag_p50: med(lags), lag_p95: pct(lags, 0.95), lag_n: lags.length,
    vstreams: vs.length, vstreams_undecodable: undec, fps_speaker: per.length ? r1(per[0].fps) : null,
    fps_tiles: per.length > 1 ? r1(med(per.slice(1).map(x => x.fps))) : null, fps_all: per.map(x => r1(x.fps)),
    freezes_min: vmin ? r1(fz / vmin) : null, astreams: as.length, conceal_pct: tot ? r1(con / tot * 100) : null,
    audio_gaps_min: amin ? r1(gaps / amin) : null, packets_lost: { video: lostV, audio: lostA },
  };
}

// direkte Kindprozesse (Wurzel je gestartetem Chromium)
function kidsOf(pid) {
  const out = [];
  for (const d of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(d)) continue;
    try { const st = fs.readFileSync('/proc/' + d + '/stat', 'utf8'); if (st.slice(st.lastIndexOf(') ') + 2).split(' ')[1] === String(pid)) out.push(d); } catch (e) { /* weg */ }
  }
  return out;
}
// CPU je Browser (/proc, Kinder des Browser-Prozesses)
function cpuTicks(rootPid) {
  const info = {};
  for (const d of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(d)) continue;
    try { const st = fs.readFileSync('/proc/' + d + '/stat', 'utf8'), rest = st.slice(st.lastIndexOf(') ') + 2).split(' '); info[d] = { ppid: rest[1], t: (+rest[11]) + (+rest[12]) }; } catch (e) { /* weg */ }
  }
  const kids = {}; Object.keys(info).forEach(k => { (kids[info[k].ppid] = kids[info[k].ppid] || []).push(k); });
  let t = 0; const walk = k => { if (!info[k]) return; t += info[k].t; (kids[k] || []).forEach(walk); };
  walk(String(rootPid));
  return t;
}

// --- Ablauf -----------------------------------------------------------------------------------------------------------
(async () => {
  const R = { t: new Date().toISOString(), call: S.call, browsers: {}, notes: [] };
  const ps = [], brs = [];
  let rc = 1;
  try {
    for (let i = 0; i < S.users.length; i++) {
      const args = ['--use-fake-ui-for-media-stream', '--ignore-certificate-errors', '--autoplay-policy=no-user-gesture-required', '--no-proxy-server',
        '--disable-features=WebRtcHideLocalIpsWithMdns', '--host-resolver-rules=' + (S.host_rules || '')];
      const o = { channel: process.env.PW_CHANNEL || 'chromium', headless: true, args };
      if (S.eng && S.eng.i === i) { o.executablePath = S.eng.exe; delete o.channel; }
      else if (process.env.BR_EXE) { o.executablePath = process.env.BR_EXE; delete o.channel; }
      const before = new Set(kidsOf(process.pid));
      const br = await chromium.launch(o);
      brs.push(br);
      br.__pid = kidsOf(process.pid).find(k => !before.has(k)) || null;
      const ctx = await br.newContext({ viewport: { width: 1100, height: 700 }, locale: 'de-DE', ignoreHTTPSErrors: true, bypassCSP: true });
      await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
      const p = await ctx.newPage();
      p.__name = 'W' + (i + 1); p.__br = br;
      p.on('pageerror', e => log('[pageerror ' + p.__name + '] ' + e.message));
      if (process.env.DEBUG) p.on('console', m => log('[console ' + p.__name + '] ' + m.text()));
      await p.addInitScript(INIT, [HUES[i % 6], FREQ[i % 6], W, H, FPS]);
      await p.goto(BASE + '/login');
      await p.fill('input[name=username], input#username, input[type=text]', S.users[i]);
      await p.fill('input[type=password]', S.pass);
      await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
      await p.waitForTimeout(500);
      await p.evaluate(() => fetch('/api/first-steps', { method: 'POST', credentials: 'same-origin', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' },
        body: JSON.stringify({ answer: 'no' }) })).catch(() => {});
      await p.goto(BASE + '/chats'); await p.waitForTimeout(2500);
      if (await p.$('.ct-setup-go')) {
        await p.click('.ct-setup-go'); await p.waitForSelector('#ct-rk');
        const rk = (await p.textContent('#ct-rk')).trim();
        await p.click('.hk-actions .btn-primary'); await p.fill('.ct-rk-last', rk.slice(-4));
        await p.click('.ct-setup-done'); await p.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 90000 });
      }
      await p.waitForTimeout(800);
      const c = await p.evaluate(() => window.HKCalls.caps()).catch(() => null);
      const dev = await p.evaluate(() => window.HKChat.state().device).catch(() => null);
      if (c && dev) await p.evaluate(([d, c]) => window.HK.api('PUT', '/api/calls/caps', { device: d, caps: c }), [dev, c]).catch(() => {});
      ps.push(p);
      log(p.__name, 'bereit');
    }
    // Beitritt (wie die Leiste "Beitreten"): Messschleife laeuft schon, Zeit bis erstes Bild/Ton
    const tJoin = {};
    for (const p of ps) {
      await p.evaluate(() => window.__m.start());
      tJoin[p.__name] = Date.now();
      await p.evaluate(([id, v]) => window.HKCalls.join(id, 'member', v), [S.call, !!S.video]);
      await sleep(800);
    }
    const first = {};
    await Promise.all(ps.map(async p => {
      const o = first[p.__name] = { video_ms: null, audio_ms: null };
      const end = Date.now() + 45000;
      while (Date.now() < end && (o.audio_ms == null || (o.video_ms == null && ps.length > 1 && S.video))) {
        const q = await capped(p.evaluate(() => window.__m.s[window.__m.s.length - 1] || null)).catch(() => null);
        if (q) {
          if (o.video_ms == null && q.lag.some(v => v != null && v < 3000)) o.video_ms = q.t - tJoin[p.__name];
          if (o.audio_ms == null && Object.values(q.a).some(x => x[1] > 0)) o.audio_ms = q.t - tJoin[p.__name];
        }
        await sleep(200);
      }
    }));
    R.first = first;
    log('Beitritt', JSON.stringify(first));
    // Dauerphase: Messwerte ab hier (Aufbau verworfen)
    for (const p of ps) await p.evaluate(() => window.__m.stop());
    for (const p of ps) await p.evaluate(() => window.__m.start());
    const pids = ps.map(p => p.__br.__pid);
    const c0 = pids.map(x => (x ? cpuTicks(x) : 0)), tS = Date.now();
    await sleep((S.secs || 30) * 1000);
    const c1 = pids.map(x => (x ? cpuTicks(x) : 0)), dt = (Date.now() - tS) / 1000;
    for (let i = 0; i < ps.length; i++) {
      const p = ps[i];
      const s = await capped(p.evaluate(() => window.__m.stop()), 30000);
      const h = await capped(p.evaluate(() => { const x = window.HKCalls.horn(); return x && { node: x.node, path: x.path, mode: x.mode, up: x.up, tiles: (x.tiles || []).length, errs: x.errs, key_n: x.key_n, known: (x.known || []).length }; }));
      const st = await capped(p.evaluate(() => window.HKCalls.hornStats()), 5000).catch(() => null);
      R.browsers[p.__name] = Object.assign({ first: first[p.__name], horn: h, e2e: st, cpu_pct: pids[i] ? Math.round((c1[i] - c0[i]) / 100 / dt * 1000) / 10 : null }, evalPage(s) || { none: true });
    }
    const names = Object.keys(R.browsers);
    const all = k => names.map(n => R.browsers[n][k]).filter(x => x != null);
    R.sum = { n: names.length, lag_p50: med(all('lag_p50')), lag_p95: all('lag_p95').length ? Math.max(...all('lag_p95')) : null,
      fps_speaker_min: all('fps_speaker').length ? Math.min(...all('fps_speaker')) : null, fps_tiles_min: all('fps_tiles').length ? Math.min(...all('fps_tiles')) : null,
      freezes_min_max: all('freezes_min').length ? Math.max(...all('freezes_min')) : null, conceal_pct_max: all('conceal_pct').length ? Math.max(...all('conceal_pct')) : null,
      first_video_ms_max: Math.max(0, ...names.map(n => (first[n] || {}).video_ms || 0)), first_audio_ms_max: Math.max(0, ...names.map(n => (first[n] || {}).audio_ms || 0)) };
    rc = 0;
  } catch (e) {
    R.error = String(e && e.stack || e).slice(0, 2000);
    log('Fehler', R.error);
  } finally {
    for (const p of ps) await capped(p.evaluate(() => { const b = document.querySelector('.cl-win .cl-round.cl-red'); if (b) b.click(); }), 3000).catch(() => {});
    await sleep(500);
    for (const b of brs) await capped(b.close(), 10000).catch(() => {});
    fs.mkdirSync(path.dirname(S.out), { recursive: true });
    fs.writeFileSync(S.out, JSON.stringify(R, null, 1));
    log('Ergebnis', S.out, JSON.stringify(R.sum || R.error).slice(0, 600));
    process.exit(rc);
  }
})();
