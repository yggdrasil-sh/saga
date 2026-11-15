// Messlauf Engpass: ein Geraet unter Test (DUT) auf TURN-TLS bzw. ICE-TCP durch den Engpass (e2e/engpass), weitere
// Teilnehmende auf TURN-TLS direkt am Knoten (ohne Engpass). Chromium = libwebrtc-Stellvertreter. Uebernommen aus dem
// Spike ws-apps (wsmess.mjs), ohne Tunnel-Wege.
//
//   node e2e/stau.mjs --path turn|tcp --secs 60 --peers 1 [--label x] [--out datei.json] [--bulk 15]
//        [--base https://127.0.0.1:8531] [--eng 8532] [--engctl 127.0.0.1:8533] [--nodepid <pid>] [--bulkurl https://...]
//
// Voraussetzung: Knoten (HORN_DEV=1) auf --base, engpass mit Lauschport --eng -> Knoten und Steuerung --engctl.
// Ergebnis: JSON mit Kennzahlen je Richtung (DUT empfaengt = Downlink, Gegenstelle empfaengt vom DUT = Uplink),
// Latenzen (Zeitmarke im Bild, Pieps), Sicht des Knotens auf die Beine des DUT, CPU je Prozess.
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
const { chromium } = createRequire(import.meta.url)('playwright'); // NODE_PATH=$(npm root -g)

const arg = (k, d) => { const i = process.argv.indexOf('--' + k); return i > 0 ? process.argv[i + 1] : d; };
const base = arg('base', 'https://127.0.0.1:8531');
const path = arg('path', 'turn');
const secs = +arg('secs', 60);
const warm = +arg('warm', 15);
const npeers = +arg('peers', 1);
const bulk = +arg('bulk', 0); // Sekunde, ab der ein grosser Download ueber denselben Engpass laeuft (0 = keiner)
const label = arg('label', path);
const out = arg('out', '');
const tiles = +arg('tiles', 3);
const engPort = arg('eng', '8532');
const engCtl = 'http://' + arg('engctl', '127.0.0.1:8533');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.error('[mess]', ...a);

process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
const pids = {};
if (arg('nodepid', '')) pids.node = +arg('nodepid');

function cpu(pid) {
  try {
    const f = fs.readFileSync(`/proc/${pid}/stat`, 'utf8').split(') ')[1].split(' ');
    return (+f[11] + +f[12]) / 100; // utime+stime in s (CLK_TCK 100)
  } catch { return null; }
}
function chromeCPU() {
  let t = 0;
  for (const d of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(d)) continue;
    try {
      const cmd = fs.readFileSync(`/proc/${d}/cmdline`, 'utf8');
      if (cmd.includes('pw-browsers') || cmd.includes('chrom')) t += cpu(+d) || 0;
    } catch { /* weg */ }
  }
  return t;
}
function withKids(pid) { // Prozess samt direkten Kindern (nginx-Worker)
  let t = cpu(pid) || 0;
  for (const d of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(d)) continue;
    try { const f = fs.readFileSync(`/proc/${d}/stat`, 'utf8').split(') ')[1].split(' '); if (+f[1] === pid) t += cpu(+d) || 0; } catch { /* weg */ }
  }
  return t;
}
const loadAvg = () => { try { return +fs.readFileSync('/proc/loadavg', 'utf8').split(' ')[0]; } catch { return null; } };
const cpuAll = () => { const o = { chrome: chromeCPU() }; for (const [k, p] of Object.entries(pids)) o[k] = withKids(p); o.t = Date.now(); return o; };

const browser = await chromium.launch({
  args: ['--ignore-certificate-errors', '--no-proxy-server', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
    '--autoplay-policy=no-user-gesture-required', '--disable-features=WebRtcHideLocalIpsWithMdns', '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'],
});
const room = 'stau-' + Date.now();
const hues = [0, 120, 240, 60, 300];
async function join(peer, p, hue, extra = '') {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  await ctx.grantPermissions(['microphone', 'camera'], { origin: new URL(base).origin });
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error') log(`[${peer}]`, m.text()); });
  await page.goto(`${base}/dev/?room=${room}&peer=${peer}&path=${p}&hue=${hue}&loud=1&mark=1&jb=${arg('jb', '-1')}&tiles=${tiles}${extra}`);
  await page.waitForFunction(() => window.__sfu && (window.__sfu.ready || window.__sfu.error), null, { timeout: 30000 });
  const err = await page.evaluate(() => window.__sfu.error);
  if (err) throw new Error(peer + ': ' + err);
  return page;
}
const pages = [];
const ppath = arg('ppath', 'turn'); // Weg der weiteren Teilnehmenden (Lastlauf: alle ueber den Tunnel)
const pextra = arg('w', '') ? '&w=' + arg('w') : '';
for (let i = 1; i <= npeers; i++) pages.push(await join('p' + i, ppath, hues[i], pextra));
await sleep(1500);
const dutExtra = { turn: '&turnhost=127.0.0.1:' + engPort, tcp: '&candport=' + engPort }[path] || '';
const tJoin = Date.now();
const dut = await join('dut', path, hues[0], dutExtra + (arg('w', '') ? '&w=' + arg('w') : ''));
pages.unshift(dut);
const tReady = Date.now();
log(`DUT ${path} beigetreten in ${tReady - tJoin} ms`);
const rep = () => Promise.all(pages.map((pg) => pg.evaluate(() => window.__sfuReport())));
for (let t = 0; t < 30; t++) {
  const r = await rep();
  if (r.every((x) => Object.values(x.legs).every((l) => l.state === 'connected'))) break;
  await sleep(500);
}
let bulkProc = null;
await sleep(warm * 1000);
await fetch(engCtl + '/stats?reset=1').catch(() => {});
const r0 = await rep();
const c0 = cpuAll();
const samples = [];
// --at "30:befehl" (mehrfach): Befehl zur Sekunde x des Messfensters (nginx neu laden, Verbindung kappen ...)
const events = process.argv.flatMap((a, i) => a === '--at' ? [process.argv[i + 1]] : []).map((x) => { const k = x.indexOf(':'); return { at: +x.slice(0, k), cmd: x.slice(k + 1) }; });
const step = +arg('step', 5);
const t0 = Date.now();
while (Date.now() - t0 < secs * 1000) {
  await sleep(step * 1000);
  for (const ev of events) {
    if (!ev.done && Date.now() - t0 >= ev.at * 1000) { ev.done = true; log('Ereignis', ev.cmd); spawn('sh', ['-c', ev.cmd], { stdio: 'ignore' }); }
  }
  if (bulk > 0 && !bulkProc && Date.now() - t0 >= bulk * 1000) {
    // grosser Download ueber denselben Engpass (eigene Quelle hinter dem Engpass, --bulkurl)
    bulkProc = spawn('curl', ['-sk', '-o', '/dev/null', arg('bulkurl', 'http://127.0.0.1:8534/big.bin')], { stdio: 'ignore' });
    log('Download gestartet');
  }
  const s = await rep();
  const eng = await fetch(engCtl + '/stats').then((r) => r.json()).catch(() => null);
  samples.push({ t: Date.now() - t0, dut: s[0], peers: s.slice(1), eng, load: loadAvg() });
}
const r1 = await rep();
const c1 = cpuAll();
// Sicht des Knotens auf die Beine des DUT (Schaetzung, Proben, Warteschlange)
const knot = {};
for (const [role, l] of Object.entries(r1[0].legs)) {
  if (!l.id) continue;
  const j = await fetch(`${base}/api/sfu/leg/${l.id}`).then((r) => r.json()).catch(() => null);
  if (j) knot[role] = { est: j.est_kbit, probes: j.probes && { started: j.probes.started, passed: j.probes.passed, failed: j.probes.failed },
    stalls: j.stats && j.stats.stalls, drops: j.stats && j.stats.frames_dropped, maxAgeMs: j.stats && j.stats.max_age_ms, slots: (j.slots || []).map((x) => [x.cls, x.layer, x.paused]).slice(0, 4), raw: j.stats, tcp: j.tcp_stau };
}
if (bulkProc) bulkProc.kill();
const nodeT = await fetch(base + '/api/dev/turnpipe').then((r) => r.json()).catch(() => null);
const eng = await fetch(engCtl + '/stats').then((r) => r.json()).catch(() => null);

// Kennzahlen
const dt = (c1.t - c0.t) / 1000;
const q = (arr, p) => { if (!arr.length) return null; const s = [...arr].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.floor(p * (s.length - 1)))]; };
const stat = (arr) => arr && arr.length ? { n: arr.length, med: q(arr, 0.5), p95: q(arr, 0.95), min: q(arr, 0), max: q(arr, 1) } : null;
function recvSummary(a, b, srcHue) {
  // Bild: Platz mit dem Farbton der Quelle (Sprecher bzw. Kachel mit den meisten Bildern)
  const vids = b.recv.map((x, j) => ({ x, p: a.recv[j] })).filter((o) => o.x.kind === 'video' && o.p);
  let best = null;
  for (const o of vids) {
    const fr = o.x.framesDecoded - o.p.framesDecoded;
    const near = o.x.markHue != null && Math.min(Math.abs(o.x.markHue - srcHue), 360 - Math.abs(o.x.markHue - srcHue)) < 30;
    if (near && (!best || fr > best.fr)) best = { fr, o };
  }
  if (!best) for (const o of vids) { const fr = o.x.framesDecoded - o.p.framesDecoded; if (!best || fr > best.fr) best = { fr, o }; }
  const v = best ? best.o : null;
  const audios = b.recv.map((x, j) => ({ x, p: a.recv[j] })).filter((o) => o.x.kind === 'audio' && o.p && o.x.samples > o.p.samples);
  const au = audios.sort((m, n) => (n.x.samples - n.p.samples) - (m.x.samples - m.p.samples))[0];
  const res = {};
  if (v) {
    const x = v.x, p = v.p;
    res.video = { fps: +((x.framesDecoded - p.framesDecoded) / dt).toFixed(1), height: x.height, kbit: Math.round((x.bytes - p.bytes) * 8 / dt / 1000),
      freezes: x.freezes - p.freezes, freezeS: +(x.freezeDur - p.freezeDur).toFixed(2), lost: x.lost - p.lost, recvd: x.recvd - p.recvd,
      jitterMs: Math.round(x.jitter * 1000), jbMs: x.jbe - p.jbe > 0 ? Math.round((x.jbd - p.jbd) / (x.jbe - p.jbe) * 1000) : null, firstFrame: x.firstFrame, pauses: x.pauses - p.pauses };
  }
  if (au) {
    const x = au.x, p = au.p;
    const ds = x.samples - p.samples;
    res.audio = { concealPct: +((x.concealed - p.concealed) / ds * 100).toFixed(2), concealEv: x.concealEv - p.concealEv, lost: x.lost - p.lost, recvd: x.recvd - p.recvd,
      jitterMs: Math.round(x.jitter * 1000), jbMs: x.jbe - p.jbe > 0 ? Math.round((x.jbd - p.jbd) / (x.jbe - p.jbe) * 1000) : null, kbit: Math.round((x.bytes - p.bytes) * 8 / dt / 1000), firstBeep: x.firstBeep, mid: x.mid };
  }
  return res;
}
// Latenzen im Messfenster: Listen am Ende minus am Anfang (Laenge)
function latDelta(a, b, kind, key) {
  const A = (a.lat[kind] || {}), B = (b.lat[kind] || {});
  const out = {};
  for (const k of Object.keys(B)) out[k] = B[k].slice(Math.max(0, (A[k] || []).length - Math.max(0, B[k].length - 4000)));
  return key ? out[key] || [] : out;
}
function vidLat(a, b, hue) {
  const all = latDelta(a, b, 'video');
  let best = [];
  for (const [k, v] of Object.entries(all)) {
    const h = +k;
    if (Number.isFinite(h) && Math.min(Math.abs(h - hue), 360 - Math.abs(h - hue)) < 30 && v.length > best.length) best = v;
  }
  return best;
}
function audLat(a, b, mid) { const all = latDelta(a, b, 'audio'); return all['a' + mid] || []; }
const down = recvSummary(r0[0], r1[0], hues[1]);
const up = recvSummary(r0[1], r1[1], hues[0]);
if (down.audio) down.audio.lat = stat(audLat(r0[0], r1[0], down.audio.mid));
if (up.audio) up.audio.lat = stat(audLat(r0[1], r1[1], up.audio.mid));
if (down.video) down.video.lat = stat(vidLat(r0[0], r1[0], hues[1]));
if (up.video) up.video.lat = stat(vidLat(r0[1], r1[1], hues[0]));
const cpuS = {};
for (const k of Object.keys(c1)) if (k !== 't' && c1[k] != null && c0[k] != null) cpuS[k] = +((c1[k] - c0[k]) / dt * 100).toFixed(1);
const legs = r1[0].legs;
const result = { label, path, secs, peers: npeers, at: new Date().toISOString(), joinMs: tReady - tJoin,
  legs: Object.fromEntries(Object.entries(legs).map(([k, l]) => [k, { t_conn: l.t_conn, rtt: l.rtt, cand: l.cand, out: l.out, rin: l.rin }])),
  down, up, knot, cpuPct: cpuS, eng, node: nodeT, loadMax: Math.max(...samples.map((s) => s.load || 0)), samples: samples.map((s) => ({ t: s.t, eng: s.eng, load: s.load,
    dutRtt: Object.values(s.dut.legs).map((l) => l.rtt), dutState: Object.values(s.dut.legs).map((l) => l.state),
    dutRecv: s.dut.recv.filter((x) => x.kind === 'video').map((x) => x.framesDecoded), peerRecv: s.peers[0] && s.peers[0].recv.filter((x) => x.kind === 'video').map((x) => x.framesDecoded), dutOut: Object.values(s.dut.legs).map((l) => (l.out || []).map((o) => [o.rid, o.w, o.fps, o.ql, o.tgt])) })) };
await browser.close();
const txt = JSON.stringify(result, null, 1);
if (out) fs.writeFileSync(out, txt);
const brief = { label, knot: Object.fromEntries(Object.entries(knot).map(([k, v]) => [k, { est: v.est, stalls: v.stalls, drops: v.drops, maxAgeMs: v.maxAgeMs, slots: v.slots }])), joinMs: result.joinMs, tconn: Object.values(result.legs).map((l) => l.t_conn), cand: Object.values(result.legs).map((l) => l.cand),
  rtt: Object.values(result.legs).map((l) => l.rtt), down, up, cpu: cpuS, loadMax: result.loadMax, engQ: eng && { up: eng.up.qdelay_max_ms, down: eng.down.qdelay_max_ms } };
console.log(JSON.stringify(brief));
