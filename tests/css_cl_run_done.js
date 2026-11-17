// 2.5 Rang 2 (T1, Pruefer): Leiste .cl-run ("Hierher übernehmen") ueber dem beendeten Anruffenster (.cl-win.cl-done) -
// Desktop und Handy (390 px): die Leiste ist klickbar, Meldung und "Schließen" des Fensters bleiben frei (Fenster mit
// Fehler bleibt stehen, z. B. "Übernahme hat nicht geklappt …"). Ohne Server, nur static/calls.css.
//   NODE_PATH=$(npm root -g) PLAYWRIGHT=playwright PW_CHANNEL=chromium node tests/css_cl_run_done.js [calls.css]
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path');
const css = fs.readFileSync(process.argv[2] || path.join(__dirname, '..', 'static', 'calls.css'), 'utf8');
const VARS = ':root{--card:#fff;--fg:#000;--border:#ccc;--fs-base:16px;--cl-bg:#111;--cl-fg:#fff;--cl-line:#333;--card-h:#eee;--cl-green:#0a0;--cl-btn:#222;--muted:#666}';
const res = [];
const ok = (n, c, info) => { res.push(!!c); console.log((c ? 'OK   ' : 'FAIL ') + n + (c || info === undefined ? '' : ' -> ' + JSON.stringify(info))); };
(async () => {
  const br = await chromium.launch({ channel: process.env.PW_CHANNEL || 'chromium', headless: true });
  for (const [name, w, h] of [['Desktop', 1200, 800], ['Handy', 390, 760]]) {
    const p = await br.newPage({ viewport: { width: w, height: h } });
    await p.setContent(`<html><head><style>${VARS}${css}</style></head><body>
      <div class="cl-win cl-done"><div class="cl-top">x</div><div></div>
        <div class="cl-status">Übernahme hat nicht geklappt – der Anruf läuft auf dem anderen Gerät weiter.</div>
        <div class="cl-ctl"><button class="cl-pill" id="close">Schließen</button></div></div>
      <div class="cl-run"><span class="cl-run-ic">📞</span><span class="cl-run-t">Anruf läuft auf Handy · Bert</span>
        <button class="cl-pill">Hinzufügen</button><button class="cl-pill cl-run-go">Hierher übernehmen</button></div></body></html>`);
    const r = await p.evaluate(() => {
      const hit = (sel) => { const b = document.querySelector(sel).getBoundingClientRect(); return document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); };
      const g = hit('.cl-run-go'), c = hit('#close'), s = hit('.cl-status');
      return { go: !!(g && g.classList.contains('cl-run-go')), close: !!(c && c.id === 'close'), status: !!(s && s.closest('.cl-win')) };
    });
    ok(`${name}: "Hierher übernehmen" liegt über dem beendeten Fenster`, r.go, r);
    ok(`${name}: "Schließen" und Meldung des Fensters nicht verdeckt`, r.close && r.status, r);
    await p.close();
  }
  await br.close();
  const n = res.filter(Boolean).length;
  console.log(`\nCSS-CL-RUN-DONE ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
  process.exit(n === res.length ? 0 : 1);
})();
