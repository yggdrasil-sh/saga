// Bild Admin -> Anrufe (Saga 2.5, A3 Tunnel; Vorher/Nachher). Aus tests/a3_tunnel.py bzw. von Hand:
//   A3_ADMIN=http://127.0.0.1:8523 A3_COOKIE=<hk_admin> A3_OUT=<datei.png> [A3_CLICK=1] [A3_HOST_RULES="MAP saga.example.org 127.0.0.1"]
//   NODE_PATH=$(npm root -g) PLAYWRIGHT=playwright PW_CHANNEL=chromium node tests/a3_bilder.js
// A3_CLICK=1: "Jetzt pruefen" (Proxy reicht WebSocket durch?) klicken und das Ergebnis abwarten. Bild: Karte #horn
// (ganze Seite zusaetzlich als <datei>-seite.png), hell, Desktop; A3_MOBILE=1 zusaetzlich Handy-Breite.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const E = process.env;
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 800))); }

(async () => {
  const args = ['--no-proxy-server', '--ignore-certificate-errors'];
  if (E.A3_HOST_RULES) args.push('--host-resolver-rules=' + E.A3_HOST_RULES);
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true, args });
  const u = new URL(E.A3_ADMIN);
  for (const mobile of E.A3_MOBILE ? [false, true] : [false]) {
    const ctx = await br.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 900 }, deviceScaleFactor: mobile ? 2 : 1,
      isMobile: mobile, locale: 'de-DE', extraHTTPHeaders: { 'Accept-Language': 'de-DE' }, ignoreHTTPSErrors: true });
    await ctx.addCookies([{ name: 'hk_admin', value: E.A3_COOKIE, domain: u.hostname, path: '/' }]);
    const p = await ctx.newPage();
    const errs = [];
    p.on('console', (m) => { if (m.type() === 'error' && !/WebSocket connection .* failed/.test(m.text())) errs.push(m.text()); });
    p.on('pageerror', (e) => errs.push(String(e)));
    const r = await p.goto(E.A3_ADMIN + '/calls', { waitUntil: 'load' });
    const out = mobile ? E.A3_OUT.replace(/\.png$/, '-handy.png') : E.A3_OUT;
    if (E.A3_CLICK && await p.$('.tu-btn')) {
      await p.click('.tu-btn');
      await p.waitForFunction(() => { const c = document.querySelector('.tu-check'); const t = c.querySelector('.tu-res').textContent; return t !== c.dataset.run && !c.querySelector('.tu-btn').disabled; }, null, { timeout: 20000 });
      const txt = await p.textContent('.tu-res');
      ok(`Pruefung "Proxy reicht WebSocket durch?": ${txt.trim().slice(0, 60)}`, /^Ja/.test(txt.trim()), txt);
    }
    const ow = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    const card = await p.$('#horn');
    if (card) await card.screenshot({ path: out });
    await p.screenshot({ path: out.replace(/\.png$/, '-seite.png'), fullPage: true });
    ok(`Admin -> Anrufe ${mobile ? 'Handy' : 'Desktop'}: Seite laedt (${r.status()}), keine Konsolenfehler, kein waagrechtes Scrollen (${ow}px)`,
      r.status() === 200 && !errs.length && ow <= 0, errs);
    await ctx.close();
  }
  await br.close();
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.log('FAIL Abbruch: ' + e); process.exit(1); });
