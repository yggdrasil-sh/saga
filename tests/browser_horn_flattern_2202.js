// Bugs-DB 2202 "Flattern unter CPU-Last": Browser-Teil der Reihe tests/horn_flattern_2202.py (zwei Knoten: horn-1 mit UDP
// und der eingebaute Knoten im Tunnel-Betrieb). Echte Saga-Oberflaeche, Kamera/Mikrofon-Attrappe wie browser_b2_frames.js.
// A und B: Chromium per "ip netns exec FL_NS" (netem-Jitter am veth); A in der cgroup FL_CG, nach dem Aufbau CPU-Last:
//    CDP Emulation.setCPUThrottlingRate FL_CPU_RATE und/oder cpu.max = FL_CG_MAX. B ohne Last (Vergleich: nur Jitter).
// Gemessen ueber FL_SECS (Standard 300): Knoten/Weg alle 2 s, Wechsel (HKCalls.horn().switches), Messungen je Knoten,
// CPU-Grenze des Kodierers (Diagnose-Proben qlr). FL_ALT_DIR: calls.js/horn.js der Gegenprobe (Playwright-Route).
//   SAGA_BASE=… SAGA_USERS=a,b SAGA_PASS=… HOST_RULES='MAP … ' FL_NS=… FL_CG=… FL_OUT=… node tests/browser_horn_flattern_2202.js
'use strict';
const pw = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const { ohneEinfuehrung } = require('./lib/erste_schritte');
const E = process.env, BASE = E.SAGA_BASE, USERS = (E.SAGA_USERS || '').split(',').filter(Boolean);
if (!BASE || USERS.length < 2 || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USERS (2), SAGA_PASS setzen'); process.exit(2); }
const SECS = +(E.FL_SECS || 300), RATE = +(E.FL_CPU_RATE || 0), CGMAX = E.FL_CG_MAX || '', H1 = E.FL_H1 || 'horn-1.example.org';
// FL_VID = BreitexHoehexBildrate, Endung n: Blockrauschen statt Flaechen (teuer fuer den Kodierer, CPU-Grenze)
const VID = (E.FL_VID || '1280x720x30').replace(/n$/, '').split('x').map(Number), NOISE = /n$/.test(E.FL_VID || '');
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 3000))); }
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn().catch(() => null); if (v) return v; await sleep(step); } return v; }
const HUES = [0, 120], FREQ = [300, 540], L = 'AB';

// Kamera/Mikrofon-Attrappe (Farbe und Ton je Person) wie browser_b2_frames.js, Bildgroesse/-rate FL_VID (Kodierlast)
const FAKE = ([hue, freq, w, h, fps, noise]) => {
  const md = navigator.mediaDevices;
  let ac = null, aStream = null, vTrack = null;
  const video = () => {
    if (vTrack && vTrack.readyState === 'live') return vTrack.clone();
    const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
    const g = cv.getContext('2d'); let f = 0;
    const nc = document.createElement('canvas'); nc.width = 80; nc.height = 45;
    const ng = nc.getContext('2d'), id = ng.createImageData(80, 45);
    setInterval(() => { f++; g.fillStyle = `hsl(${hue} 85% 50%)`; g.fillRect(0, 0, w, h); g.fillStyle = '#fff';
      if (noise) {
        for (let i = 0; i < id.data.length; i += 4) { const v = Math.random() * 255; id.data[i] = v; id.data[i + 1] = (v + hue) % 255; id.data[i + 2] = 255 - v; id.data[i + 3] = 255; }
        ng.putImageData(id, 0, 0); g.imageSmoothingEnabled = false; g.drawImage(nc, 0, 0, w, h * 0.8);
      }
      for (let i = 0; i < 8; i++) g.fillRect((f * 7 + i * 97) % (w - 40), (i * 61 + f * 3) % (h - 40), 40, 40); }, 1000 / fps);
    vTrack = cv.captureStream(fps).getVideoTracks()[0]; return vTrack;
  };
  const audio = () => {
    if (!ac) { ac = new AudioContext(); const o = ac.createOscillator(); o.frequency.value = freq; const gn = ac.createGain(); gn.gain.value = 0.3; const d = ac.createMediaStreamDestination(); o.connect(gn).connect(d); o.start(); aStream = d.stream; }
    return aStream.getAudioTracks()[0].clone();
  };
  md.getUserMedia = async c => { const s = new MediaStream(); if (c && c.audio) s.addTrack(audio()); if (c && c.video) s.addTrack(video()); return s; };
};
const HANG = () => { const b = document.querySelector('.cl-win .cl-round.cl-red'); if (b) b.click(); return !!b; };
const ARGS = ['--use-fake-ui-for-media-stream', '--ignore-certificate-errors', '--autoplay-policy=no-user-gesture-required', '--no-proxy-server',
  '--host-resolver-rules=' + (E.HOST_RULES || '')];

// Browser A: Startskript, das den Browser in die cgroup legt und im Netz-Namensraum startet (Playwright-Pipe bleibt)
const WRAPS = [];
function wrapper(i) {
  if (!E.FL_NS) return undefined;
  const exe = pw.chromium.executablePath();
  const f = path.join(E.FL_WRAP_DIR || os.tmpdir(), 'fl2202-chromium-' + process.pid + '-' + L[i] + '.sh');
  WRAPS.push(f);
  fs.writeFileSync(f, '#!/bin/sh\n' + (E.FL_CG && i === 0 ? `echo $$ > ${E.FL_CG}/cgroup.procs\n` : '') + `exec ip netns exec ${E.FL_NS} ${exe} "$@"\n`, { mode: 0o755 });
  return f;
}
async function session(i) {
  const br = await pw.chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true, args: ARGS, executablePath: wrapper(i) });
  const ctx = await br.newContext({ viewport: { width: 1100, height: 700 }, locale: 'de-DE', ignoreHTTPSErrors: true, bypassCSP: true });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  if (E.FL_ALT_DIR) {                                  // Gegenprobe: Web-Client vor dem Fix (nur diese zwei Dateien)
    for (const f of ['calls.js', 'horn.js']) {
      const body = fs.readFileSync(path.join(E.FL_ALT_DIR, f));
      await ctx.route(new RegExp('/static/' + f.replace('.', '\\.') + '(\\?|$)'), r => r.fulfill({ status: 200, contentType: 'application/javascript', body }));
    }
  }
  const p = await ctx.newPage();
  p.__br = br;
  p.on('pageerror', e => console.log('  [pageerror ' + L[i] + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + L[i] + '] ' + m.text()));
  await p.addInitScript(FAKE, [HUES[i], FREQ[i], i === 0 ? VID[0] : 640, i === 0 ? VID[1] : 360, i === 0 ? VID[2] : 15, i === 0 && NOISE]);
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', USERS[i]);
  await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(500);
  await ohneEinfuehrung(p);                            // Frage "Erste Schritte" (frische Konten ca-fl*), Bugs-DB 2428
  return p;
}
async function chatKeys(q) {
  await q.goto(BASE + '/chats'); await q.waitForTimeout(2500);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim();
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 120000 });
  }
  await q.waitForTimeout(800);
}
const horn = p => p.evaluate(() => window.HKCalls && window.HKCalls.horn()).catch(() => null);
async function api(p, m, url, body) { return p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, msg: e.message })), [m, url, body]); }
const legsUp = h => h && h.up && Object.keys(h.legs).length > 0;
async function answer(p, id) {
  const rang = await p.waitForSelector('.cl-ring', { timeout: 20000 }).then(() => true, () => false);
  if (rang) await p.click('.cl-ring .cl-big'); else await p.evaluate(id => window.HKCalls.join(id, 'member', true), id);
}
const med = a => { const s = a.slice().sort((x, y) => x - y), n = s.length; return !n ? null : n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; };
const jit = a => { if (a.length < 2) return 0; let s = 0; for (let i = 1; i < a.length; i++) s += Math.abs(a[i] - a[i - 1]); return s / (a.length - 1); };
// Sende-Proben der Diagnose je Schicht: Median Bildhoehe, fps, kbit/s, Kodierzeit; Gruende der Begrenzung
const encOf = ss => { const o = {}; ss.forEach(x => { const k = x.rid || '-'; (o[k] = o[k] || []).push(x); });
  return Object.fromEntries(Object.entries(o).map(([k, a]) => [k, { n: a.length, h: med(a.map(x => x.h || 0)), fps: med(a.map(x => x.fps || 0)),
    kbps: med(a.map(x => x.kbps || 0)), encms: med(a.filter(x => x.encms != null).map(x => x.encms)),
    qlr: a.reduce((q, x) => { q[x.qlr || '?'] = (q[x.qlr || '?'] || 0) + 1; return q; }, {}) }])); };
const short = n => String(n || '-').split('.')[0];
const measOf = h => Object.fromEntries(Object.entries((h && h.meas) || {}).map(([n, m]) => [short(n), m && m.rtts && m.rtts.length
  ? { n: m.rtts.length, med: Math.round(med(m.rtts)), jit: Math.round(jit(m.rtts)), udp: m.udp != null ? 1 : m.udpMaybe ? '?' : 0, ulog: (m.ulog || []).join(',') } : null]));

(async () => {
  let rc = 1;
  const ps = [];
  const out = { secs: SECS, cpu_rate: RATE, cg_max: CGMAX, vid: VID, alt: !!E.FL_ALT_DIR, samples: [], A: null, B: null };
  try {
    ps.push(await session(0));                         // einzeln: scheitert B, wird A im finally trotzdem geschlossen
    ps.push(await session(1));
    for (const p of ps) await chatKeys(p);
    const devs = [];
    for (const p of ps) devs.push(await until(() => p.evaluate(() => window.HKChat && window.HKChat.state() && window.HKChat.state().device), 15000));
    const mk = await api(ps[0], 'POST', '/api/chats', { kind: 'free', members: [USERS[1]], device: devs[0] });
    ok('freie Gruppe A+B', mk.ok, mk);
    const gid = mk.r.chat.id;
    for (const p of ps) await p.goto(BASE + '/chats#c' + gid);
    await ps[0].waitForSelector('#ct-chat:not([hidden])');
    for (let i = 0; i < 2; i++) { const c = await ps[i].evaluate(() => window.HKCalls.caps()); await api(ps[i], 'PUT', '/api/calls/caps', { device: devs[i], caps: c }); }
    await until(() => ps[0].evaluate(() => { const v = document.getElementById('ct-call-v'); return v && !v.hidden; }), 15000);
    await ps[0].waitForTimeout(1500);
    let t0 = Date.now();
    await ps[0].click('#ct-call-v');
    const ha = await until(async () => { const h = await horn(ps[0]); return legsUp(h) && h; }, 40000);
    ok(`A: Anruf steht (${Date.now() - t0} ms) auf ${ha && ha.node}, Weg ${ha && ha.path}`, ha && ha.node === H1 && ha.path === 'udp', ha && { node: ha.node, path: ha.path, log: ha.log, meas: measOf(ha) });
    t0 = Date.now();
    await answer(ps[1], ha.id);
    const hb = await until(async () => { const h = await horn(ps[1]); return legsUp(h) && h.tiles.length >= 1 && h; }, 40000);
    // B (nur Jitter) startet je nach Beitritts-Probe auch mal auf dem Tunnel-Knoten (Probe 800 ms bei RTT ~70 +- 50 ms knapp) -
    // dann holt ihn der Rueckweg (2201) nach DWELL zurueck; gezaehlt wird unten nur das Flattern (Hinweg UDP -> Tunnel)
    ok(`B: beigetreten (${Date.now() - t0} ms) auf ${hb && hb.node}, Weg ${hb && hb.path}`, !!hb, hb && { node: hb.node, path: hb.path });
    await sleep(5000);

    // --- Last an ---------------------------------------------------------------------------------------------------
    if (RATE > 1) { const cdp = await ps[0].context().newCDPSession(ps[0]); await cdp.send('Emulation.setCPUThrottlingRate', { rate: RATE }); ps[0].__cdp = cdp; }
    if (CGMAX && E.FL_CG && fs.existsSync(path.join(E.FL_CG, 'cpu.max'))) fs.writeFileSync(path.join(E.FL_CG, 'cpu.max'), CGMAX);
    const tLoad = Date.now();
    console.log(`  Last an: CPU-Drossel ${RATE || '-'}x, cpu.max ${CGMAX || '-'}; ${SECS} s`);
    let lastPrint = 0;
    while (Date.now() - tLoad < SECS * 1000) {
      const [a, b] = await Promise.all([horn(ps[0]), horn(ps[1])]);
      const s = { t: Math.round((Date.now() - tLoad) / 1000), a: a && { node: short(a.node), path: a.path, sw: a.switches.length, rtt: a.net && a.net.rtt, q: a.q },
                  b: b && { node: short(b.node), path: b.path, sw: b.switches.length } };
      out.samples.push(s);
      if (Date.now() - lastPrint >= 30000) {
        lastPrint = Date.now();
        console.log(`  t=${s.t}s A ${s.a && s.a.node}/${s.a && s.a.path} sw=${s.a && s.a.sw} rtt=${s.a && s.a.rtt} meas=${JSON.stringify(measOf(a))} | B ${s.b && s.b.node}/${s.b && s.b.path} sw=${s.b && s.b.sw}`);
      }
      await sleep(2000);
    }
    if (E.FL_CG) try {                                 // wirkt die cgroup? (Prozesse darin, gedrosselte Zeit)
      const st = Object.fromEntries(fs.readFileSync(path.join(E.FL_CG, 'cpu.stat'), 'utf8').trim().split('\n').map(l => l.split(' ')));
      out.cg = { procs: fs.readFileSync(path.join(E.FL_CG, 'cgroup.procs'), 'utf8').trim().split('\n').filter(Boolean).length,
                 usage_s: Math.round(st.usage_usec / 1e5) / 10, throttled_s: Math.round((st.throttled_usec || 0) / 1e5) / 10, nr_throttled: +st.nr_throttled || 0 };
      console.log('  cgroup A: ' + JSON.stringify(out.cg));
    } catch (e) { console.log('  cgroup: ' + e.message); }
    if (ps[0].__cdp) await ps[0].__cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 }).catch(() => {});
    if (CGMAX && E.FL_CG) try { fs.writeFileSync(path.join(E.FL_CG, 'cpu.max'), 'max 100000'); } catch (e) { /* weg */ }
    for (let i = 0; i < 2; i++) {
      const h = await horn(ps[i]);
      const sw = (h && h.switches) || [];
      const per = {};
      out.samples.forEach(s => { const x = s[i ? 'b' : 'a']; const k = x ? x.node + '/' + x.path : '-'; per[k] = (per[k] || 0) + 1; });
      const outS = ((h && h.diag && h.diag.samples) || []).filter(x => x.dir === 'out');
      out[L[i]] = { switches: sw.map(x => ({ t: Math.round((x.t - tLoad) / 1000), from: short(x.from), to: short(x.to), why: x.why, path: x.path, fromPath: x.fromPath })),
                    better_load: sw.filter(x => x.why === 'better' && x.t >= tLoad).map(x => ({ t: Math.round((x.t - tLoad) / 1000), from: short(x.from), to: short(x.to), fromPath: x.fromPath, path: x.path })),
                    share: Object.entries(per).map(([k, v]) => k + ' ' + Math.round(v * 100 / Math.max(1, out.samples.length)) + '%').join(', '),
                    cpu_samples: outS.filter(x => x.qlr === 'cpu').length, out_samples: outS.length, qcpu_ms: outS.reduce((s, x) => s + (x.qcpu || 0), 0),
                    enc: encOf(outS), meas: measOf(h), node: h && short(h.node), path: h && h.path, log: h && h.log && h.log.slice(-12) };
      console.log(`  ${L[i]}: Wechsel ${JSON.stringify(out[L[i]].switches)}; Knotenzeit ${out[L[i]].share}; CPU-Grenze ${out[L[i]].cpu_samples}/${out[L[i]].out_samples} Proben (${out[L[i]].qcpu_ms} ms); Senden ${JSON.stringify(out[L[i]].enc)}`);
    }
    ok('A war waehrend der Last im Anruf (Knoten in jeder Probe)', out.samples.every(s => s.a && s.a.node && s.a.node !== '-'), out.samples.filter(s => !s.a || !s.a.node).slice(0, 5));
    await ps[0].evaluate(HANG);
    rc = res.every(Boolean) ? 0 : 1;
  } catch (e) {
    console.log('FAIL Ausnahme: ' + (e && e.stack || e));
    res.push(false);
  } finally {
    if (E.FL_OUT) try { fs.writeFileSync(E.FL_OUT, JSON.stringify(out, null, 1)); } catch (e) { console.log('FL_OUT: ' + e.message); }
    for (const p of ps) if (p) await p.__br.close().catch(() => {});
    for (const f of WRAPS) try { fs.unlinkSync(f); } catch (e) { /* schon weg */ }
  }
  console.log(`\nBROWSER-HORN-FLATTERN-2202 ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(rc);
})();
