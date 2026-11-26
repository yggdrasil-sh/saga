// Bugs-DB 2026: Saga hinter Node64 im Browser (aus tests/node64_labor.py): Seite ueber Node64 (HTTP/2?), Live-Kanal
// (EventSource) und WebSocket /api/sfu/ws (saga-frames.1) aus der Seite heraus.
//   N64_BASE=https://saga.example.org:18651 N64_COOKIE=<saga-Sitzung> N64_HOST_RULES="MAP saga.example.org 127.0.0.1" node tests/browser_node64.js
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const E = process.env;
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 800))); }

(async () => {
  const args = ['--no-proxy-server', '--ignore-certificate-errors'];
  if (E.N64_HOST_RULES) args.push('--host-resolver-rules=' + E.N64_HOST_RULES);
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true, args });
  const u = new URL(E.N64_BASE);
  const ctx = await br.newContext({ ignoreHTTPSErrors: true, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: E.N64_COOKIE, domain: u.hostname, path: '/', secure: true }]);
  const p = await ctx.newPage();
  const r = await p.goto(E.N64_BASE + '/login', { waitUntil: 'load' });
  const proto = await p.evaluate(() => (performance.getEntriesByType('navigation')[0] || {}).nextHopProtocol);
  console.log('    Protokoll Browser -> Node64: ' + proto);
  ok('Browser: Anmeldeseite ueber Node64 (' + r.status() + ', ' + proto + ')', r.status() === 200);
  const es = await p.evaluate(() => new Promise((done) => {
    const t0 = performance.now(); const s = new EventSource('/api/events');
    s.onopen = () => { const ms = Math.round(performance.now() - t0); s.close(); done({ open: true, ms }); };
    s.onerror = () => { s.close(); done({ open: false }); };
    setTimeout(() => done({ open: false, timeout: true }), 5000);
  }));
  ok('Browser: EventSource /api/events ueber Node64 offen (' + es.ms + ' ms)', es.open, es);
  const ws = await p.evaluate(() => new Promise((done) => {
    const w = new WebSocket(location.origin.replace(/^https/, 'wss') + '/api/sfu/ws', 'saga-frames.1');
    w.onopen = () => { const pr = w.protocol; w.close(); done({ open: true, protocol: pr }); };
    w.onerror = () => done({ open: false });
    setTimeout(() => done({ open: false, timeout: true }), 8000);
  }));
  ok('Browser: WebSocket /api/sfu/ws (saga-frames.1) ueber Node64 offen', ws.open && ws.protocol === 'saga-frames.1', ws);
  await br.close();
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.log('FAIL Abbruch: ' + e); process.exit(1); });
