// Messlauf B2 (gjallarhorn/e2e/b2mess, nach dem Spike ws-browser/b2-vorab): N Geraete im Testraum des Knotens, je Geraet Weg
// ws (Produktfassung static/horn-frames.js + frame-e2e.js) bzw. turn (TURN-TLS am Knoten) und Zugang (shaper|direkt).
// Aufruf: node call.mjs '<json>'   z. B. {"who":[{"path":"ws","via":"shaper"},{"path":"turn"},{"path":"turn"}],"dur":30,
//          "warm":8,"set":"down=1500&rtt=50","tag":"ws-1.5M"}   (engpass: kbit/s, rtt ms, qms, loss)
// Ausgabe: eine JSON-Zeile je Geraet (Zusammenfassung) + Datei $B2M_DIR/runs/<tag>.json (Rohdaten). Immer unter
// flock /tmp/ws-mess.lock aufrufen (WSB_FLOCK=1).
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require((process.env.NODE_PATH || '/usr/lib/node_modules') + '/playwright');
const fs = require('node:fs');
const { execSync } = require('node:child_process');

const P = JSON.parse(process.argv[2] || '{}');
const who = P.who || [{ path: 'ws', via: 'shaper' }, { path: 'turn' }];
const dur = P.dur || 30, warm = P.warm || 8;
const tag = P.tag || 'lauf';
const room = 'r' + Date.now();
const hues = [0, 120, 220, 300, 60, 180];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.error('[lauf]', ...a);
// Ports/Ordner wie b2stand.sh (B2M_DIR, B2M_BASE; Standard /tmp/b2/stand, 8590)
const WD = process.env.B2M_DIR || '/tmp/b2/stand', WB = +(process.env.B2M_BASE || 8590);
const PORT = { ngx: WB + 1, node: WB + 4, shNgx: WB + 6, shTurn: WB + 7, ctl: WB + 8 };
const ctl = (q) => execSync(`curl -s "http://127.0.0.1:${PORT.ctl}/set?${q}"`).toString();

// Messsperre mit ws-apps (2 Kerne): /tmp/ws-mess.lock per mkdir, sonst warten. WSB_FLOCK=1: Aufrufer haelt die
// Sperre schon (flock /tmp/ws-mess.lock node call.mjs ...), dann hier nichts.
const LOCK = '/tmp/ws-mess.lock';
if (!P.nolock && !process.env.WSB_FLOCK) {
  let waited = 0;
  for (;;) {
    try { fs.mkdirSync(LOCK); break; } catch (e) { if (waited % 60 === 0) log('Sperre belegt, warte …'); await sleep(5000); waited += 5; }
  }
  const free = () => { try { fs.rmdirSync(LOCK); } catch (e) { /* */ } };
  process.on('exit', free); process.on('SIGINT', () => { free(); process.exit(130); }); process.on('SIGTERM', () => { free(); process.exit(143); });
}
if (P.set !== undefined) log('shaper', ctl(P.set || 'down=0&up=0&rtt=0&loss=0&qms=300').trim());

// CPU je Prozess (Jiffies) fuer Knoten, saga-media, nginx und Chromium gesamt
const HZ = 100;
function jiff(pid) { try { const s = fs.readFileSync(`/proc/${pid}/stat`, 'utf8').split(') ')[1].split(' '); return +s[11] + +s[12]; } catch (e) { return 0; } }
function pidsOf(name) { try { return execSync(`pgrep -x ${name}`).toString().trim().split('\n').map(Number).filter(Boolean); } catch (e) { return []; } }
const rd = (f) => { try { return +fs.readFileSync(f, 'utf8').trim(); } catch (e) { return 0; } };
// nur eigene Prozesse: nginx-Meister aus nginx.pid samt Kindern, Chromium = Nachfahren des eigenen Browser-Prozesses
function kids(pid) { try { return execSync(`pgrep -P ${pid}`).toString().trim().split('\n').map(Number).filter(Boolean); } catch (e) { return []; } }
function tree(pid) { const out = [pid]; for (let i = 0; i < out.length; i++) out.push(...kids(out[i])); return out; }
let BPID = 0;
function cpuSnap() {
  const node = rd(`${WD}/run/node.pid`), media = rd(`${WD}/run/media.pid`), shaper = rd(`${WD}/run/shaper.pid`);
  const ng = tree(rd(`${WD}/nginx/nginx.pid`));
  let chrome = 0;
  if (BPID) for (const p of tree(BPID)) if (p !== BPID) chrome += jiff(p);
  return { t: Date.now(), node: jiff(node), media: jiff(media), nginx: ng.reduce((a, p) => a + jiff(p), 0), shaper: jiff(shaper), chrome };
}

const browser = await chromium.launch({
  channel: 'chromium', headless: true, ignoreDefaultArgs: P.sound ? ['--mute-audio'] : [],
  args: ['--ignore-certificate-errors', '--no-proxy-server', '--use-fake-ui-for-media-stream', ...(P.sound ? [] : ['--use-fake-device-for-media-stream']),
    '--autoplay-policy=no-user-gesture-required', '--disable-features=WebRtcHideLocalIpsWithMdns', ...(P.args || [])],
});
BPID = process.pid; // Playwright startet Chromium als Kind dieses Prozesses
// b2-vorab: Geraete mit "browser":"firefox" (FF=Pfad zum Linux-Build 1543, Herkunft 127.0.0.1 statt localhost wegen ICE)
let ffBrowser = null;
if (who.some((w) => w.browser === 'firefox')) {
  const { firefox } = require((process.env.NODE_PATH || '/usr/lib/node_modules') + '/playwright');
  const env = { ...process.env }; for (const k of Object.keys(env)) if (/proxy/i.test(k)) delete env[k];
  if (process.env.FF_PULSE) env.PULSE_SERVER = process.env.FF_PULSE;   // Firefox headless: ohne Audio-Ausgabe bleibt der AudioContext stehen
  ffBrowser = await firefox.launch({ headless: true, executablePath: process.env.FF, env, firefoxUserPrefs: {
    'media.navigator.streams.fake': true, 'media.navigator.permission.disabled': true, 'media.autoplay.default': 0, 'media.autoplay.blocking_policy': 0 } });
}
const pages = [];
const t0 = Date.now();
for (const [i, w] of who.entries()) {
  const ctx = await (w.browser === 'firefox' ? ffBrowser : browser).newContext({ ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  const peer = 'p' + i;
  page.on('console', (m) => { if (m.type() === 'error' && !/favicon|404/.test(m.text())) log(`[${peer}]`, m.text().slice(0, 300)); });
  const shaped = w.via === 'shaper';
  const host = w.browser === 'firefox' ? '127.0.0.1' : 'localhost';
  const origin = w.origin || (shaped ? `https://${host}:${PORT.shNgx}` : `https://${host}:${PORT.ngx}`);
  const turn = shaped ? `localhost:${PORT.shTurn}` : `localhost:${PORT.node}`;
  const q = new URLSearchParams({ room, peer, path: w.path, hue: hues[i % hues.length], tiles: w.tiles ?? 3, turn, loud: i === 0 ? 1 : 0, ...(P.q || {}), ...(w.q || {}) });
  await page.goto(`${origin}/b2mess/b2.html?${q}`);
  pages.push({ page, peer, ...w });
}
for (const p of pages) {
  await p.page.waitForFunction(() => window.__sfu && (window.__sfu.ready || window.__sfu.error), null, { timeout: 30000 });
  const err = await p.page.evaluate(() => window.__sfu.error);
  if (err) log(p.peer, 'FEHLER', err);
}
const tJoin = Date.now() - t0;
log('alle beigetreten nach', tJoin, 'ms; Anlauf', warm, 's');
await sleep(warm * 1000);
if (P.during) log('shaper (Messfenster)', ctl(P.during).trim());
let dl = null;
if (P.download) dl = require('node:child_process').spawn('curl', ['-sk', '-o', '/dev/null', '--limit-rate', P.download, `https://localhost:${PORT.shNgx}/big/blob.bin`]);
const rep0 = await Promise.all(pages.map((p) => p.page.evaluate(() => window.__rep())));
const c0 = cpuSnap();
const steps = [];
const n = Math.max(1, Math.round(dur / 5));
for (let k = 0; k < n; k++) {
  await sleep(dur * 1000 / n);
  steps.push(await Promise.all(pages.map((p) => p.page.evaluate(() => window.__rep()))));
  if (P.stepSet && P.stepSet[k]) log('shaper', ctl(P.stepSet[k]).trim());
  if (P.stepCmd && P.stepCmd[k]) { log('befehl', P.stepCmd[k]); try { log(execSync(P.stepCmd[k]).toString().trim()); } catch (e) { log('befehl fehlgeschlagen', String(e)); } }
}
const c1 = cpuSnap();
if (dl) dl.kill();
const shst = JSON.parse(execSync(`curl -s http://127.0.0.1:${PORT.ctl}/stats`).toString());
const rep1 = steps[steps.length - 1];
const roomInfo = JSON.parse(execSync(`curl -sk https://localhost:${PORT.ngx}/api/dev/room/${room}`).toString() || '{}');
await browser.close();
if (ffBrowser) await ffBrowser.close();

const pct = (a, q) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(q * (s.length - 1) + 0.5))]; };
const secs = (c1.t - c0.t) / 1000;
const cpu = Object.fromEntries(['node', 'media', 'nginx', 'shaper', 'chrome'].map((k) => [k, Math.round((c1[k] - c0[k]) / HZ / secs * 1000) / 10]));
const out = { load: require('node:os').loadavg().map((x) => Math.round(x * 100) / 100), tag, room, who: who.map((w) => w.path + (w.via === 'shaper' ? '*' : '')), dur: secs, join_ms: tJoin, cpu_pct: cpu, shaper: shst, peers: [] };
const peerOf = Object.fromEntries(Object.entries(roomInfo.peers || {}).map(([k, v]) => [v, k]));
for (const [i, p] of pages.entries()) {
  const a = rep0[i], b = rep1[i];
  const allLat = steps.flatMap((s) => s[i].lat), allBeep = steps.flatMap((s) => s[i].beeps);
  const rws = steps.flatMap((s) => (s[i].ws && s[i].ws.ws.video && s[i].ws.ws.video.rtts) || []);
  const sum = { rttWs50: pct(rws, 0.5), rttWs95: pct(rws, 0.95), peer: p.peer, path: p.path, via: p.via || 'direkt', first: a.first, ws: b.ws && { audio: { ...b.ws.ws.audio, rtts: undefined }, video: { ...b.ws.ws.video, rtts: undefined }, up: b.ws.up, aq: b.ws.aq, loop: b.ws.loop, C: P.verbose ? b.ws.C : undefined }, send: b.send, srcFps: b.srcFps, e2e: b.ws && b.ws.e2e, e2eC: b.ws && Object.fromEntries(Object.entries(b.ws.C || {}).filter(([k]) => /e2e|csrc/.test(k))), fe: b.fe && { drop: b.fe.drop, dec: b.fe.dec, enc: b.fe.enc, keyreq: b.fe.keyreq }, rtt: b.rtt, video: [], audio: [] };
  for (const [j, x] of b.recv.entries()) {
    const y = a.recv.find((r) => r.slot === x.slot) || {};
    if (x.kind === 'video') {
      const lat = allLat.filter((l) => l[1] === x.slot).map((l) => l[2]).filter((v) => v < 5000);
      const hs = allLat.filter((l) => l[1] === x.slot).map((l) => l[3]);
      const ts = allLat.filter((l) => l[1] === x.slot).map((l) => l[0]).sort((u, v) => u - v);
      let gap = 0, gapAt = 0; for (let k = 1; k < ts.length; k++) if (ts[k] - ts[k - 1] > gap) { gap = ts[k] - ts[k - 1]; gapAt = ts[k - 1]; }
      sum.video.push({ slot: x.slot, fps: Math.round((x.dec - (y.dec || 0)) / secs * 10) / 10, kbps: Math.round((x.bytes - (y.bytes || 0)) * 8 / secs / 1000), h: x.h, hMed: pct(hs, 0.5),
        frz: x.frz - (y.frz || 0), frzS: Math.round(((x.frzS || 0) - (y.frzS || 0)) * 100) / 100, drop: x.drop - (y.drop || 0), keys: x.keys - (y.keys || 0),
        jb: x.jbe > (y.jbe || 0) ? Math.round(1000 * (x.jbd - (y.jbd || 0)) / (x.jbe - (y.jbe || 0))) : null, lat50: pct(lat, 0.5), lat95: pct(lat, 0.95), latN: lat.length, hue: x.hue, gapMax: gap, gapAt });
    } else {
      const bl = allBeep.filter((l) => l[1] === x.slot).map((l) => l[2]);
      const bt = allBeep.filter((l) => l[1] === x.slot).map((l) => l[0]).sort((u, v) => u - v);
      let bgap = 0; for (let k = 1; k < bt.length; k++) bgap = Math.max(bgap, bt[k] - bt[k - 1]);
      const ds = x.samples - (y.samples || 0), dc = x.conc - (y.conc || 0);
      const lv = steps.map((st) => (st[i].recv.find((r) => r.slot === x.slot) || {}).level).filter((v) => v != null);
      const lvl = pct(lv, 0.5), lvdb = lvl > 0 ? Math.round(20 * Math.log10(lvl) * 10) / 10 : -100;
      const pk = steps.map((st) => (st[i].recv.find((r) => r.slot === x.slot) || {}).peakDb).filter((v) => v != null && isFinite(v));
      sum.audio.push({ slot: x.slot, kbps: Math.round((x.bytes - (y.bytes || 0)) * 8 / secs / 1000), conc: ds ? Math.round(1000 * dc / ds) / 10 : null, concEv: x.ev - (y.ev || 0),
        jb: x.jbe > (y.jbe || 0) ? Math.round(1000 * (x.jbd - (y.jbd || 0)) / (x.jbe - (y.jbe || 0))) : null, beep50: pct(bl, 0.5), beep95: pct(bl, 0.95), beepN: bl.length, beepGap: bgap, lvlDb: lvdb, peakDbMed: pct(pk, 0.5), peakHz: x.peakHz, peakDb: x.peakDb });
    }
  }
  sum.ev = b.ev.slice(-12);
  out.peers.push(sum);
}
fs.mkdirSync(`${WD}/runs`, { recursive: true });
fs.writeFileSync(`${WD}/runs/${tag}.json`, JSON.stringify({ out, steps: steps.map((s) => s.map((r) => ({ ...r, lat: undefined, beeps: undefined }))) }, null, 1));
console.log(JSON.stringify(out));
