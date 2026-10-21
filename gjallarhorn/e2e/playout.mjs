// playout-delay (P9): 2 Geraete ueber ICE-TCP, Empfaenger OHNE jitterBufferTarget (wie die Apps). Gemessen wird der
// mittlere Ziel- und tatsaechliche Jitter-Puffer der Bildempfaenger (getStats, inbound-rtp) ueber 10 s. Mit dem Knoten
// (HORN_PLAYOUT_DELAY Standard 150,400) soll das Ziel >= ~140 ms sein, ohne (HORN_PLAYOUT_DELAY=0) deutlich darunter.
// Aufruf: node e2e/playout.mjs <basis-url>   -> JSON {jb_target_ms, jb_delay_ms, jb_min_ms}
import { createRequire } from 'module';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT || 'playwright');

const base = (process.argv[2] || 'https://localhost:18544').replace(/\/$/, '');
const room = 'pd-' + Date.now();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
const browser = await chromium.launch({ args: ['--ignore-certificate-errors', '--no-proxy-server', '--use-fake-device-for-media-stream',
  '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required', '--disable-features=WebRtcHideLocalIpsWithMdns'] });
const pages = [];
for (const [i, hue] of [0, 200].entries()) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  await ctx.grantPermissions(['microphone', 'camera'], { origin: new URL(base).origin });
  const page = await ctx.newPage();
  await page.goto(`${base}/dev/?room=${room}&peer=p${i}&path=tcp&hue=${hue}&jb=-1`);
  pages.push(page);
}
for (const page of pages) await page.waitForFunction(() => window.__sfu && (window.__sfu.ready || window.__sfu.error), null, { timeout: 30000 });
await sleep(10000);
const vid = async () => (await pages[0].evaluate(() => window.__sfuReport())).recv.filter((x) => x.kind === 'video' && x.jbe > 0);
const a = await vid();
await sleep(10000);
const b = await vid();
let t = 0, d = 0, e = 0, m = 0;
for (const x of b) {
  const y = a.find((z) => z.mid === x.mid) || { jbt: 0, jbd: 0, jbe: 0, jbm: 0 };
  t += x.jbt - y.jbt; d += x.jbd - y.jbd; m += x.jbm - y.jbm; e += x.jbe - y.jbe;
}
await browser.close();
const r = { frames: e, jb_target_ms: e ? Math.round(t / e * 1000) : null, jb_delay_ms: e ? Math.round(d / e * 1000) : null, jb_min_ms: e ? Math.round(m / e * 1000) : null };
console.log(JSON.stringify(r));
process.exit(e > 0 ? 0 : 1);
