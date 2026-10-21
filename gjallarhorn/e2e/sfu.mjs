// Ende-zu-Ende-Lauf des Weiterleiters in headless Chromium (Playwright):
// 4 Geraete im selben Raum, 2 ueber ICE-TCP und 2 ueber TURN-TLS. Jedes muss
// die anderen 3 sehen (Farbton) und hoeren (Ton-Energie, CSRC). Danach wird
// der Downlink eines Geraets im Knoten gedrosselt: Der Sprecherplatz muss von
// der oberen auf die untere Schicht wechseln (Layout und Bildhoehe).
// Aufruf: node e2e/sfu.mjs <basis-url>   (z. B. https://localhost:18543)
import { chromium } from 'playwright';

const base = (process.argv[2] || 'https://localhost:18543').replace(/\/$/, '');
const room = 'e2e-' + Date.now();
const people = [
  { peer: 'p0', path: 'tcp', hue: 0 },
  { peer: 'p1', path: 'turn', hue: 120, loud: true },
  { peer: 'p2', path: 'tcp', hue: 220 },
  { peer: 'p3', path: 'turn', hue: 300 },
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.error('[e2e]', ...a);
const fail = [];
const check = (ok, msg) => { if (!ok) { fail.push(msg); log('FEHLER', msg); } else log('ok', msg); };

async function node(method, url, body) {
  const r = await fetch(base + url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  return r.json();
}

process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0'; // nur lokaler Testknoten mit eigenem Zertifikat
const browser = await chromium.launch({
  args: ['--ignore-certificate-errors', '--no-proxy-server', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
    '--autoplay-policy=no-user-gesture-required', '--disable-features=WebRtcHideLocalIpsWithMdns'],
});
const pages = [];
for (const p of people) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  await ctx.grantPermissions(['microphone', 'camera'], { origin: new URL(base).origin });
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error') log(`[${p.peer}]`, m.text()); });
  await page.goto(`${base}/dev/?room=${room}&peer=${p.peer}&path=${p.path}&hue=${p.hue}&loud=${p.loud ? 1 : 0}`);
  pages.push(page);
}
for (const [i, page] of pages.entries()) {
  await page.waitForFunction(() => window.__sfu && (window.__sfu.ready || window.__sfu.error), null, { timeout: 30000 });
  const err = await page.evaluate(() => window.__sfu.error);
  check(!err, `${people[i].peer}: beigetreten ${err || ''}`);
}
const reports = async () => Promise.all(pages.map((pg) => pg.evaluate(() => window.__sfuReport())));
// verbinden und anlaufen lassen
let rep;
for (let t = 0; t < 40; t++) {
  await sleep(1000);
  rep = await reports();
  if (rep.every((r) => Object.values(r.legs).every((l) => l.state === 'connected'))) break;
}
for (const r of rep) check(Object.values(r.legs).every((l) => l.state === 'connected'), `${r.peer} (${r.path}): beide Leitungen verbunden ${JSON.stringify(r.legs)}`);
await sleep(12000);
const csrcOf = (await node('GET', `/api/dev/room/${room}`)).peers;
const peerOf = Object.fromEntries(Object.entries(csrcOf).map(([k, v]) => [v, k]));
rep = await reports();
await sleep(3000);
const rep2 = await reports();
for (const [i, r] of rep2.entries()) {
  const me = people[i];
  const others = people.filter((p) => p.peer !== me.peer);
  // Ton: drei Plaetze mit den drei anderen; Spitzenfrequenz = Absender, CSRC passt
  const heard = new Set();
  r.recv.filter((x) => x.kind === 'audio').forEach((x, j) => {
    const prev = rep[i].recv.filter((y) => y.kind === 'audio')[j];
    if (!(x.samples > (prev ? prev.samples : 0)) || x.peakDb < -90) return;
    const o = others.find((p) => Math.abs(200 + p.hue - x.peakHz) < 15);
    if (o && x.csrc.some((c) => peerOf[c] === o.peer)) heard.add(o.peer);
    else log(`${me.peer}: tonplatz ${x.mid} ${x.peakHz} Hz ${x.peakDb} dB csrc ${x.csrc.map((c) => peerOf[c])}`);
  });
  check(others.every((o) => heard.has(o.peer)), `${me.peer} hoert ${[...heard].join(',')} (soll ${others.map((o) => o.peer).join(',')})`);
  // Bild: jeder andere in irgendeinem Platz, Rahmen laufen, Farbton passt
  const seen = new Set();
  r.recv.filter((x) => x.kind === 'video').forEach((x, j) => {
    const prev = rep[i].recv.filter((y) => y.kind === 'video')[j];
    if (x.framesDecoded <= (prev ? prev.framesDecoded : 0) || x.hue == null) return;
    const o = others.find((p) => Math.min(Math.abs(p.hue - x.hue), 360 - Math.abs(p.hue - x.hue)) < 25);
    if (o) seen.add(o.peer);
  });
  check(others.every((o) => seen.has(o.peer)), `${me.peer} sieht ${[...seen].join(',')} (soll ${others.map((o) => o.peer).join(',')})`);
}
// Schichtwechsel: p0 (ICE-TCP) sieht p1 als Sprecher in der oberen Schicht
const p0 = rep2[0];
const lay = async () => (await node('GET', `/api/sfu/leg/${p0.legs.video.id}`));
let l0 = await lay();
for (let t = 0; t < 15 && !(l0.slots[0].peer === 'p1' && l0.slots[0].layer === 'm'); t++) { await sleep(1000); l0 = await lay(); }
check(l0.slots[0].peer === 'p1' && l0.slots[0].layer === 'm', `p0: Sprecher p1 in Schicht m (${JSON.stringify(l0.slots[0])}, ${l0.est_kbit} kbit/s)`);
const spkH = async () => (await reports())[0].recv.filter((x) => x.kind === 'video')[0].height;
const h1 = await spkH();
log('Sprecherbild bei p0 vorher', h1, 'Pixel hoch');
await node('POST', '/api/dev/throttle', { leg: p0.legs.video.id, kbit: 220 });
let l1 = await lay();
for (let t = 0; t < 20 && l1.slots[0].layer !== 'l'; t++) { await sleep(1000); l1 = await lay(); }
check(l1.slots[0].layer === 'l', `p0 gedrosselt (220 kbit/s): Sprecher auf Schicht l (${JSON.stringify(l1.slots[0])}, ${l1.est_kbit} kbit/s)`);
await sleep(4000);
const h2 = await spkH();
check(h2 > 0 && h2 < h1, `p0: Sprecherbild ${h1} -> ${h2} Pixel hoch`);
await node('POST', '/api/dev/throttle', { leg: p0.legs.video.id, kbit: 0 });
// Empfang der anderen bleibt oben
const lo = await node('GET', `/api/sfu/leg/${rep2[2].legs.video.id}`);
check(lo.slots[0].layer === 'm', `p2 ungedrosselt bleibt in Schicht m (${JSON.stringify(lo.slots[0])})`);
const load = await node('GET', '/api/dev/load');
log('knoten', JSON.stringify(load.load), 'nachlaeufer', load.node.tail_probes, 'tid-ausgelassen', load.node.tid_skipped);
check(load.node.tail_probes > 0, 'Nachlaeufer gesendet');
await browser.close();
console.log(JSON.stringify({ room, fail, load: load.load }, null, 1));
process.exit(fail.length ? 1 : 0);
