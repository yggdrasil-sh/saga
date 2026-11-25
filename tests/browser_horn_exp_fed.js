// 2.6 (Bugs-DB 2263): Admin -> Foederation mit verbundenem Server ohne Marke "Experimentell" (2.5, Vorgabe 10.11.,
// Pruefer horn-exp-web: vorher mit Marke im Block - die Texte entfernt 2263-4).
// Testumgebung (zwei verbundene Instanzen, tools/testumgebung/zwei_stacks.py; Admin-Bereich = Port der Instanz + 1):
//   HX_ERWARTE=an  UMG_NS=.. UMG_DB=.. tools/testumgebung/umgebung.sh lauf fedknoten <slot> browser_horn_exp_fed.js
//   HX_ERWARTE=aus UMG_NS=.. UMG_DB=.. tools/testumgebung/umgebung.sh lauf fed <slot> browser_horn_exp_fed.js
// an (calls.horn auf A und B): je verbundenem Server ein Block "Gjallarhorn-Knoten fuer ..." ohne Marke, nirgends eine
// Marke oder "experimentell", Kontrast der Blockueberschrift >= 4.5:1, kein waagrechtes Scrollen bei 390 px, keine
// Konsolenfehler; aus (calls.horn aus): kein Block, keine Marke. Bilder <HX_PREFIX>-foederation-<A|B>-<1280|390>-<hell|dunkel>.png nach SHOTS.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const path = require('path'), fs = require('fs');
const E = process.env, SHOTS = E.SHOTS || '/tmp', AN = E.HX_ERWARTE === 'an', PRE = E.HX_PREFIX || (E.HX_ERWARTE === 'an' ? 'fedknoten' : 'fed');
const res = [];
function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c || i === undefined ? '' : ' -> ' + JSON.stringify(i).slice(0, 900))); }
const adm = u => { const x = new URL(u); return 'http://127.0.0.1:' + (Number(x.port) + 1); };
(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true });
  try {
    for (const [k, url] of [['A', E.SAGA_A], ['B', E.SAGA_B]]) {
      const A = adm(url);
      for (const [scheme, phone] of [['light', false], ['dark', false], ['light', true]]) {
        const ctx = await br.newContext(phone ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, locale: 'de-DE', colorScheme: scheme }
          : { viewport: { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: scheme });
        const p = await ctx.newPage(), errs = [];
        p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); }); p.on('pageerror', e => errs.push(String(e)));
        await p.goto(A + '/login');
        await p.fill('input[name=username]', 'fedadmin'); await p.fill('input[name=password]', 'Fed-Admin-Pass-1234');
        await Promise.all([p.waitForLoadState('load'), p.press('input[name=password]', 'Enter')]);
        await p.waitForTimeout(500);
        const r = await p.goto(A + '/federation', { waitUntil: 'load' });
        const st = await p.evaluate(() => {
          function bg(e) { while (e) { const c = getComputedStyle(e).backgroundColor; if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c; e = e.parentElement; } return 'rgb(255,255,255)'; }
          function L(c) { const m = c.match(/[\d.]+/g).map(Number); const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
            return 0.2126 * f(m[0]) + 0.7152 * f(m[1]) + 0.0722 * f(m[2]); }
          const forms = [...document.querySelectorAll('form.fed-horn')];
          const tags = [...document.querySelectorAll('.adm-tag.exp')];
          const t = forms[0] && forms[0].querySelector('h4'), cr = t ? (() => { const a = L(getComputedStyle(t).color), b = L(bg(t)); return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); })() : 0;
          return { forms: forms.length, inForm: forms.filter(f => f.querySelector('h4 .adm-tag.exp')).length,
            exp: /experimentell/i.test(document.body.textContent),
            tags: tags.length, peers: document.querySelectorAll('form.fed-opts:not(.fed-horn)').length, cr: Math.round(cr * 100) / 100,
            ow: document.documentElement.scrollWidth - window.innerWidth, h4: forms[0] ? forms[0].querySelector('h4').textContent.trim() : '' };
        });
        const tag = k + '-' + (phone ? '390' : '1280') + '-' + (scheme === 'dark' ? 'dunkel' : 'hell');
        if (AN) ok(`${PRE} ${tag}: je Server Block ohne Marke, nirgends "experimentell", Kontrast >= 4.5, kein waagrechtes Scrollen, keine Konsolenfehler (${r.status()})`,
          r.status() === 200 && st.peers >= 1 && st.forms >= 1 && st.inForm === 0 && st.tags === 0 && !st.exp && st.cr >= 4.5 && st.ow <= 1 && !errs.length, { st, errs });
        else ok(`${PRE} ${tag}: Gjallarhorn aus -> kein Block, keine Marke (${r.status()})`, r.status() === 200 && st.peers >= 1 && st.forms === 0 && st.tags === 0 && !st.exp && !errs.length, { st, errs });
        console.log('  ' + tag + ' ' + JSON.stringify(st));
        const f = await p.$('form.fed-horn'); if (f) await f.scrollIntoViewIfNeeded();
        await p.screenshot({ path: path.join(SHOTS, PRE + '-foederation-' + tag + '.png') });
        await ctx.close();
      }
    }
  } catch (e) { ok('Ablauf', false, String(e && e.stack || e)); }
  await br.close();
  const n = res.filter(Boolean).length;
  console.log(`\nHORN-EXP-FED ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
  process.exit(n === res.length ? 0 : 1);
})();
