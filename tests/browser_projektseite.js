// Projektseite (docs/index.html und docs/<sprache>/index.html, GitHub Pages): alle fuenf Sprachen in Chromium und Firefox,
// 1440 und 390 Breite, hell und dunkel. Prueft: keine Konsolen- und Seitenfehler, keine fehlgeschlagenen Anfragen, alle Bilder
// geladen, kein seitliches Scrollen, Anker und relative Links, lang/hreflang/Sprachwahl, eine h1 und keine uebersprungene
// Ueberschriften-Ebene, Alt-Texte, Video ohne Anfrage vor dem Klick (fehlt die MP4, bleibt das Poster als Bild).
//   [SITE_DIR=docs] [SITE_PORT=8861] [SHOTS=<ordner>] [NUR_BILDER=1] [BROWSER=chromium,firefox] [PLAYWRIGHT=...] node tests/browser_projektseite.js
// SHOTS: ganze Seite je Sprache/Browser/Breite/Schema als JPEG (<sprache>-<browser>-<breite>-<hell|dunkel>.jpg).
// NUR_BILDER=1: nur Bilder aufnehmen, nichts pruefen (z. B. fuer den alten Stand: SITE_DIR=<alter docs-Ordner>).
const pw = require(process.env.PLAYWRIGHT || 'playwright');
const http = require('http'), fs = require('fs'), path = require('path');
const DIR = path.resolve(process.env.SITE_DIR || path.join(__dirname, '..', 'docs'));
const PORT = +(process.env.SITE_PORT || 8861), SHOTS = process.env.SHOTS, NUR = process.env.NUR_BILDER === '1';
const LANGS = ['de', 'en', 'es', 'ru', 'th'];
const BROWSERS = (process.env.BROWSER || 'chromium,firefox').split(',');
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.svg': 'image/svg+xml', '.webp': 'image/webp', '.jpg': 'image/jpeg',
  '.png': 'image/png', '.mp4': 'video/mp4', '.js': 'text/javascript' };
let fails = 0, n = 0;
const ok = (c, m, extra) => { n++; if (!c) { fails++; console.log('FEHL', m, extra === undefined ? '' : JSON.stringify(extra).slice(0, 400)); } };

const srv = http.createServer((q, r) => {
  let p = decodeURIComponent(new URL(q.url, 'http://x').pathname);
  if (p.endsWith('/')) p += 'index.html';
  const f = path.join(DIR, path.normalize(p));
  if (!f.startsWith(DIR) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { r.writeHead(404); r.end(); return; }
  r.writeHead(200, { 'Content-Type': TYPES[path.extname(f)] || 'application/octet-stream' });
  fs.createReadStream(f).pipe(r);
});

(async () => {
  await new Promise(res => srv.listen(PORT, '127.0.0.1', res));
  const base = `http://127.0.0.1:${PORT}/`;
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
  for (const bn of BROWSERS) {
    const br = await pw[bn].launch(bn === 'chromium' ? { channel: process.env.PW_CHANNEL || undefined } : {});
    for (const lang of LANGS) {
      const url = base + (lang === 'de' ? '' : lang + '/');
      for (const [w, h, mobile] of [[1440, 900, false], [390, 844, true]]) {
        for (const scheme of ['light', 'dark']) {
          const tag = `${lang} ${bn} ${w} ${scheme}`;
          const ctx = await br.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, locale: lang === 'de' ? 'de-DE' : lang,
            deviceScaleFactor: 1, ...(mobile && bn === 'chromium' ? { isMobile: true, hasTouch: true } : {}) });
          await ctx.addInitScript((l) => { try { localStorage.setItem('saga-lang', l); } catch (e) { /* egal */ } }, lang);
          const p = await ctx.newPage();
          const errs = [], bad = [], reqs = [];
          p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
          p.on('pageerror', e => errs.push(String(e)));
          p.on('requestfailed', r => bad.push(r.url() + ' ' + (r.failure() || {}).errorText));
          p.on('response', r => { if (r.status() >= 400) bad.push(r.status() + ' ' + r.url()); });
          p.on('request', r => reqs.push(r.url()));
          await p.goto(url, { waitUntil: 'load' });
          // alle Bilder laden (loading=lazy sofort laden, dann auf jedes Bild warten)
          await p.evaluate(async () => { const im = [...document.images]; im.forEach(i => { i.loading = 'eager'; });
            await Promise.all(im.map(i => i.complete && i.naturalWidth ? 0 : new Promise(r => { i.addEventListener('load', r, { once: true }); i.addEventListener('error', r, { once: true }); setTimeout(r, 8000); }))); });
          await p.waitForTimeout(300);
          if (SHOTS) await p.screenshot({ path: path.join(SHOTS, `${lang}-${bn}-${w}-${scheme === 'light' ? 'hell' : 'dunkel'}.jpg`), fullPage: true, type: 'jpeg', quality: 80 });
          if (!NUR) {
            ok(!errs.length, tag + ': Konsolenfehler', errs);
            ok(!bad.length, tag + ': fehlgeschlagene Anfragen', bad);
            ok(!reqs.some(u => /\.mp4/.test(u)), tag + ': Video vor dem Klick geladen');
            const r = await p.evaluate(() => {
              const imgs = [...document.images];
              const hs = [...document.querySelectorAll('h1,h2,h3,h4')].map(e => +e.tagName[1]);
              let skip = null; hs.reduce((a, b) => { if (b > a + 1 && !skip) skip = a + '->' + b; return b; }, 1);
              return {
                lang: document.documentElement.lang,
                broken: imgs.filter(i => !i.complete || !i.naturalWidth).map(i => i.currentSrc || i.src),
                noalt: imgs.filter(i => !i.hasAttribute('alt')).map(i => i.src),
                over: document.documentElement.scrollWidth - window.innerWidth,
                anchors: [...document.querySelectorAll('a[href^="#"]')].map(a => a.getAttribute('href')).filter(hr => hr.length > 1 && !document.getElementById(hr.slice(1))),
                rel: [...new Set([...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href')).filter(hr => !/^(https?:|#|mailto:)/.test(hr)))],
                h1: document.querySelectorAll('h1').length, skip,
                alts: [...document.querySelectorAll('link[rel=alternate][hreflang]')].map(l => l.hreflang).sort().join(','),
                cur: [...document.querySelectorAll('.lang a[aria-current=page]')].map(a => a.dataset.l),
                poster: !!document.querySelector('.promo video[poster][preload=none] source[src$=".mp4"]'),
                credit: (document.querySelector('.credit') || {}).textContent || '',
              };
            });
            ok(r.lang === lang, tag + ': lang', r.lang);
            ok(!r.broken.length, tag + ': Bilder nicht geladen', r.broken);
            ok(!r.noalt.length, tag + ': Bilder ohne alt', r.noalt);
            ok(r.over <= 0, tag + ': seitliches Scrollen', r.over);
            ok(!r.anchors.length, tag + ': Anker ohne Ziel', r.anchors);
            ok(r.h1 === 1 && !r.skip, tag + ': Ueberschriften', r);
            ok(r.alts === 'de,en,es,ru,th,x-default', tag + ': hreflang', r.alts);
            ok(r.cur.length === 2 && r.cur.every(l => l === lang), tag + ': Sprachwahl', r.cur);
            ok(r.poster, tag + ': Video mit Poster und preload=none');
            ok(r.credit.includes('Music: “Surrender” by Unheard – Free To Use (freetouse.com/music)'), tag + ': Musik-Angabe', r.credit);
            for (const hr of r.rel) {
              const res = await p.request.get(new URL(hr, url).href);
              ok(res.ok(), tag + ': relativer Link ' + hr, res.status());
            }
            // Fokus sichtbar: erster Tab-Halt (Sprunglink) bekommt einen Rahmen
            if (w === 1440) {
              await p.keyboard.press('Tab');
              const f = await p.evaluate(() => { const a = document.activeElement; const cs = getComputedStyle(a); return { cls: a.className, ow: cs.outlineWidth, os: cs.outlineStyle, top: a.getBoundingClientRect().top }; });
              ok(f.cls === 'skip' && f.os !== 'none' && parseFloat(f.ow) >= 2 && f.top >= 0, tag + ': Fokus sichtbar', f);
            }
          }
          await ctx.close();
        }
      }
      console.log('fertig', lang, bn);
    }
    // Video ohne MP4: Klick auf Abspielen -> Poster bleibt als Bild, kein Skriptfehler
    if (!NUR && !fs.existsSync(path.join(DIR, 'video', 'saga-promo.mp4'))) {
      const ctx = await br.newContext({ viewport: { width: 1440, height: 900 } });
      await ctx.addInitScript(() => { try { localStorage.setItem('saga-lang', 'de'); } catch (e) { /* egal */ } });
      const p = await ctx.newPage();
      const perr = []; p.on('pageerror', e => perr.push(String(e)));
      await p.goto(base, { waitUntil: 'load' });
      await p.evaluate(() => { const v = document.querySelector('.promo video'); v.play().catch(() => {}); });
      await p.waitForTimeout(2500);
      const s = await p.evaluate(() => ({ video: !!document.querySelector('.promo video'), img: (document.querySelector('.promo .frame img') || {}).naturalWidth || 0,
        soon: (document.querySelector('.promo .soon') || {}).textContent || '' }));
      ok(!s.video && s.img > 0 && s.soon === 'Video folgt' && !perr.length, bn + ': Video fehlt -> Poster als Bild', { s, perr });
      await ctx.close();
    }
    await br.close();
  }
  srv.close();
  console.log(NUR ? 'Bilder fertig' : (fails ? `FEHLER ${fails} von ${n}` : `ALLES OK (${n} Pruefungen)`));
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
