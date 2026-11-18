// Saga-Siegel als PNG rendern (aufgerufen von tools/siegel.py png): Zeilen "<svg>\t<png>\t<px>" aus der Datei argv[2].
// Chromium (Playwright) zeichnet das SVG in genau px x px, Hintergrund durchsichtig.
'use strict';
const fs = require('fs');
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');

(async () => {
  const jobs = fs.readFileSync(process.argv[2], 'utf8').split('\n').filter(Boolean).map((l) => l.split('\t'));
  const browser = await chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const page = await browser.newPage({ deviceScaleFactor: 1, viewport: { width: 300, height: 300 } });
  for (const [svg, png, px] of jobs) {
    const n = Number(px);
    const src = 'data:image/svg+xml;base64,' + fs.readFileSync(svg).toString('base64');
    await page.setContent('<!doctype html><html><body style="margin:0;background:transparent">' +
      `<img id="s" src="${src}" width="${n}" height="${n}" style="display:block"></body></html>`);
    await page.waitForFunction(() => { const i = document.getElementById('s'); return i.complete && i.naturalWidth > 0; });
    await page.screenshot({ path: png, omitBackground: true, clip: { x: 0, y: 0, width: n, height: n } });
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
