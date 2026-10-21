// Ende-zu-Ende-Lauf der Messseite in headless Chromium (Playwright).
// Aufruf: node e2e/probe.mjs <url> [paths] [dur]
//   url   z. B. https://localhost:18443/
//   paths z. B. udp,tcp,turn (Standard: alle)
// Ergebnis (JSON) auf stdout; Exit 1, wenn ICE-TCP oder TURN-TLS nicht verbindet
// oder nichts gespeichert wurde. HORN_EXPECT=turn: nur TURN-TLS muss gehen.
import { chromium } from 'playwright';

const url = process.argv[2] || 'https://localhost:18443/';
const paths = process.argv[3] || 'udp,tcp,turn';
const dur = process.argv[4] || '12';
const expect = (process.env.HORN_EXPECT || 'tcp,turn').split(',');

const browser = await chromium.launch({
  args: ['--ignore-certificate-errors', '--no-proxy-server', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required', '--disable-features=WebRtcHideLocalIpsWithMdns'],
});
const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
await ctx.grantPermissions(['microphone', 'camera'], { origin: new URL(url).origin });
const page = await ctx.newPage();
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') console.error('[seite]', m.text()); });
const q = `?dur=${dur}&base=3&ton=${Math.max(6, Math.round(dur / 2))}&paths=${paths}&net=e2e-lokal`;
await page.goto(url + q);
await page.waitForFunction(() => window.__hornReady === true, null, { timeout: 15000 });
await page.selectOption('#dev', 'Desktop');
await page.click('#go');
await page.waitForFunction(() => window.__hornResult, null, { timeout: 15 * 60 * 1000, polling: 1000 });
const r = await page.evaluate(() => window.__hornResult);
await browser.close();
console.log(JSON.stringify(r, null, 1));
let bad = !r.file;
for (const p of expect) {
  const ph = r.phases.find((x) => x.path === p && x.profile === 'v12');
  if (!ph || !ph.ok) { console.error(`FEHLER: ${p} nicht verbunden`); bad = true; }
}
process.exit(bad ? 1 : 0);
