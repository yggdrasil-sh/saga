// Projektseite (docs/index.html und docs/<sprache>/index.html, GitHub Pages): alle fuenf Sprachen in Chromium und Firefox,
// 1440 und 390 Breite, hell und dunkel, Bewegung an und aus. Prueft: keine Konsolen- und Seitenfehler, keine fehlgeschlagenen
// Anfragen, nur Anfragen an die eigene Seite (keine CDNs/Schrift-Dienste), alle Bilder geladen, kein seitliches Scrollen, Anker
// und relative Links, lang/hreflang/Sprachwahl, eine h1 und keine uebersprungene Ueberschriften-Ebene, Alt-Texte, Video ohne
// Anfrage vor dem Klick und nach dem Klick abspielbar (fehlt die MP4, bleibt das Poster als Bild), Schriften (Fraunces, Noto Sans Runic) geladen, Animationen
// nur ueber transform/opacity, Abschnitte wachsen beim Scrollen herein (ohne Bewegung sofort sichtbar), Schalter "Bewegung"
// (Zustand, Wahl in localStorage, nach Neuladen erhalten) und prefers-reduced-motion (ohne Wahl: keine Bewegung).
//   [SITE_DIR=docs] [SITE_PORT=8861] [SHOTS=<ordner>] [NUR_BILDER=1] [BROWSER=chromium,firefox] [PLAYWRIGHT=...] node tests/browser_projektseite.js
// SHOTS: ganze Seite je Sprache/Browser/Breite/Schema als JPEG (<sprache>-<browser>-<breite>-<hell|dunkel>.jpg, Bewegung an,
//   alle Abschnitte hereingewachsen). MOTION=an|aus|an,aus (Standard beide).
// NUR_BILDER=1: nur Bilder aufnehmen, nichts pruefen (z. B. fuer den alten Stand: SITE_DIR=<alter docs-Ordner>).
const pw = require(process.env.PLAYWRIGHT || 'playwright');
const http = require('http'), fs = require('fs'), path = require('path');
const DIR = path.resolve(process.env.SITE_DIR || path.join(__dirname, '..', 'docs'));
const PORT = +(process.env.SITE_PORT || 8861), SHOTS = process.env.SHOTS, NUR = process.env.NUR_BILDER === '1';
const LANGS = ['de', 'en', 'es', 'ru', 'th'];
const BROWSERS = (process.env.BROWSER || 'chromium,firefox').split(',');
const MOTIONS = (process.env.MOTION || (NUR ? 'an' : 'an,aus')).split(',');
// erlaubte Eigenschaften in Animationen (der Gold-Schimmer eines Wortes darf background-position bewegen)
const ANIM_OK = new Set(['offset', 'easing', 'composite', 'computedOffset', 'transform', 'opacity', 'translate', 'rotate', 'scale']);
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.svg': 'image/svg+xml', '.webp': 'image/webp', '.jpg': 'image/jpeg',
  '.png': 'image/png', '.mp4': 'video/mp4', '.js': 'text/javascript', '.woff2': 'font/woff2' };
let fails = 0, n = 0;
const ok = (c, m, extra) => { n++; if (!c) { fails++; console.log('FEHL', m, extra === undefined ? '' : JSON.stringify(extra).slice(0, 400)); } };

const srv = http.createServer((q, r) => {
  let p = decodeURIComponent(new URL(q.url, 'http://x').pathname);
  if (p.endsWith('/')) p += 'index.html';
  const f = path.join(DIR, path.normalize(p));
  if (!f.startsWith(DIR) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { r.writeHead(404); r.end(); return; }
  const type = TYPES[path.extname(f)] || 'application/octet-stream', size = fs.statSync(f).size;
  const m = /^bytes=(\d*)-(\d*)$/.exec(q.headers.range || '');  // Teilanfragen wie GitHub Pages (Video)
  if (m) {
    const a = m[1] ? +m[1] : size - +m[2], b = m[1] && m[2] ? Math.min(+m[2], size - 1) : size - 1;
    r.writeHead(206, { 'Content-Type': type, 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${a}-${b}/${size}`, 'Content-Length': b - a + 1 });
    fs.createReadStream(f, { start: a, end: b }).pipe(r); return;
  }
  r.writeHead(200, { 'Content-Type': type, 'Accept-Ranges': 'bytes', 'Content-Length': size });
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
        for (const scheme of ['light', 'dark']) for (const mot of MOTIONS) {
          const tag = `${lang} ${bn} ${w} ${scheme} Bewegung ${mot}`;
          const ctx = await br.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, locale: lang === 'de' ? 'de-DE' : lang,
            deviceScaleFactor: 1, ...(mobile && bn === 'chromium' ? { isMobile: true, hasTouch: true } : {}) });
          await ctx.addInitScript(([l, m]) => { try { localStorage.setItem('saga-lang', l); if (!sessionStorage.getItem('t')) { localStorage.setItem('saga-motion', m); sessionStorage.setItem('t', 1); } } catch (e) { /* egal */ } }, [lang, mot]);
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
          if (!NUR) {
            // Abschnitte wachsen beim Scrollen herein: einmal durchscrollen, danach muss alles sichtbar sein
            const pre = await p.evaluate(() => ({ motion: document.documentElement.classList.contains('motion'),
              pressed: (document.querySelector('.mot') || {}).getAttribute && document.querySelector('.mot').getAttribute('aria-pressed'),
              hidden: [...document.querySelectorAll('.rv')].filter(e => e.getBoundingClientRect().top > innerHeight * 1.5 && getComputedStyle(e).opacity !== '1').length,
              rv: document.querySelectorAll('.rv').length }));
            ok(pre.motion === (mot === 'an') && pre.pressed === (mot === 'an' ? 'true' : 'false'), tag + ': Bewegung/Schalter', pre);
            ok(pre.rv > 30 && (mot === 'an' ? pre.hidden > 5 : pre.hidden === 0), tag + ': Hereinwachsen (unten noch verborgen nur mit Bewegung)', pre);
            // mit dem Mausrad wie ein Mensch (scrollTo allein loest in Headless-Chromium keine IntersectionObserver-Runde aus)
            await p.mouse.move(w / 2, h / 2);
            for (let i = 0, H = await p.evaluate(() => document.documentElement.scrollHeight); i * h * 0.6 < H + h; i++) { await p.mouse.wheel(0, Math.round(h * 0.6)); await p.waitForTimeout(90); }
            await p.evaluate(() => scrollTo({ top: 0, behavior: 'instant' }));
            await p.waitForTimeout(1500);
            const post = await p.evaluate(() => [...document.querySelectorAll('.rv')].filter(e => !e.classList.contains('in') || parseFloat(getComputedStyle(e).opacity) < 0.99).map(e => e.className + ' ' + getComputedStyle(e).opacity));
            ok(!post.length, tag + ': nach dem Scrollen alles sichtbar', post);
            const an = await p.evaluate((OK) => { const ANIM = new Set(OK); const bad = new Set(); let n = 0;
              for (const a of document.getAnimations()) { if (!a.effect || !a.effect.getKeyframes) continue; n++;
                const t = a.effect.target; const shim = t && t.classList && t.classList.contains('shim');
                for (const k of a.effect.getKeyframes()) for (const prop of Object.keys(k)) if (!ANIM.has(prop) && !(shim && /^backgroundPosition/.test(prop))) bad.add(prop + '@' + (t && t.className && t.className.baseVal !== undefined ? t.className.baseVal : t && t.className)); }
              return { n, bad: [...bad] }; }, [...ANIM_OK]).catch(e => ({ n: -1, bad: [String(e)] }));
            ok(!an.bad.length && (mot === 'an' ? an.n > 20 : an.n === 0), tag + ': Animationen nur transform/opacity', an);
            {
              const f = await p.evaluate(async () => { await document.fonts.ready; return { fr: document.fonts.check('600 24px Fraunces', 'Saga'), ru: document.fonts.check('20px "Noto Sans Runic"', 'ᛟ'),
                loaded: [...document.fonts].filter(x => x.status === 'loaded').map(x => x.family + ' ' + x.style + ' ' + x.weight) }; });
              ok(f.loaded.some(x => /Fraunces/.test(x)) && f.loaded.some(x => /Runic/.test(x)), tag + ': Schriften geladen', f);
            }
          }
          // Bilder: alles hereingewachsen, Terminal fertig getippt (endliche Animationen ans Ende)
          if (SHOTS) { await p.evaluate(() => document.querySelectorAll('.rv').forEach(e => e.classList.add('in'))); await p.waitForTimeout(1000);
            await p.evaluate(() => document.getAnimations().forEach(a => { try { if (a.effect.getComputedTiming().iterations !== Infinity) a.finish(); } catch (e) { /* egal */ } })); await p.waitForTimeout(200); }
          if (SHOTS && mot === MOTIONS[0]) await p.screenshot({ path: path.join(SHOTS, `${lang}-${bn}-${w}-${scheme === 'light' ? 'hell' : 'dunkel'}.jpg`), fullPage: true, type: 'jpeg', quality: 80 });
          if (!NUR) {
            ok(!errs.length, tag + ': Konsolenfehler', errs);
            ok(!bad.length, tag + ': fehlgeschlagene Anfragen', bad);
            ok(!reqs.some(u => /\.mp4/.test(u)), tag + ': Video vor dem Klick geladen');
            ok(!reqs.some(u => !u.startsWith(base) && !u.startsWith('data:')), tag + ': nur eigene Anfragen', reqs.filter(u => !u.startsWith(base)));
            const r = await p.evaluate((W) => {
              const imgs = [...document.images];
              const hs = [...document.querySelectorAll('h1,h2,h3,h4')].map(e => +e.tagName[1]);
              let skip = null; hs.reduce((a, b) => { if (b > a + 1 && !skip) skip = a + '->' + b; return b; }, 1);
              return {
                lang: document.documentElement.lang,
                broken: imgs.filter(i => !i.complete || !i.naturalWidth).map(i => i.currentSrc || i.src),
                noalt: imgs.filter(i => !i.hasAttribute('alt')).map(i => i.src),
                over: document.documentElement.scrollWidth - W,  // gegen die eingestellte Breite (Chromium mit isMobile vergroessert sonst innerWidth)
                anchors: [...document.querySelectorAll('a[href^="#"]')].map(a => a.getAttribute('href')).filter(hr => hr.length > 1 && !document.getElementById(hr.slice(1))),
                rel: [...new Set([...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href')).filter(hr => !/^(https?:|#|mailto:)/.test(hr)))],
                h1: document.querySelectorAll('h1').length, skip,
                alts: [...document.querySelectorAll('link[rel=alternate][hreflang]')].map(l => l.hreflang).sort().join(','),
                cur: [...document.querySelectorAll('.lang a[aria-current=page]')].map(a => a.dataset.l),
                poster: !!document.querySelector('.promo video[poster][preload=none] source[src$=".mp4"]'),
                credit: (document.querySelector('.credit') || {}).textContent || '',
              };
            }, w);
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
            // Kopfleiste fast deckend: ueber Nordlicht/dunklen Abschnitten bleiben ihre Links >= 4,5:1 (bei 78 % hell 3,6:1)
            const topA = await p.evaluate(() => { const c = getComputedStyle(document.querySelector('.top')).backgroundColor;
              const m = /\/\s*([\d.]+)\s*\)$/.exec(c) || /^rgba\([^)]*,\s*([\d.]+)\)$/.exec(c); return { c, a: m ? +m[1] : 1 }; });
            ok(topA.a >= 0.9, tag + ': Kopfleiste deckend', topA);
            // Fokus sichtbar: erster Tab-Halt (Sprunglink) bekommt einen Rahmen
            if (w === 1440) {
              await p.keyboard.press('Tab');
              const f = await p.evaluate(() => { const a = document.activeElement; const cs = getComputedStyle(a); return { cls: a.className, ow: cs.outlineWidth, os: cs.outlineStyle, top: a.getBoundingClientRect().top }; });
              ok(f.cls === 'skip' && f.os !== 'none' && parseFloat(f.ow) >= 2 && f.top >= 0, tag + ': Fokus sichtbar', f);
              // Schalter "Bewegung": umschalten, Wahl gemerkt, nach Neuladen erhalten
              const b0 = await p.evaluate(() => document.documentElement.classList.contains('motion'));
              await p.click('.mot');
              const b1 = await p.evaluate(() => ({ m: document.documentElement.classList.contains('motion'), pr: document.querySelector('.mot').getAttribute('aria-pressed'), ls: localStorage.getItem('saga-motion') }));
              ok(b1.m === !b0 && b1.pr === String(!b0) && b1.ls === (b0 ? 'aus' : 'an'), tag + ': Schalter Bewegung', b1);
              await p.reload({ waitUntil: 'load' });
              const b2 = await p.evaluate(() => document.documentElement.classList.contains('motion'));
              ok(b2 === !b0, tag + ': Wahl nach Neuladen erhalten', b2);
            }
          }
          await ctx.close();
        }
      }
      console.log('fertig', lang, bn);
    }
    // prefers-reduced-motion: ohne eigene Wahl keine Bewegung; mit Wahl "an" (Schalter) doch
    if (!NUR) for (const [stored, want] of [[null, false], ['an', true]]) {
      const ctx = await br.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
      await ctx.addInitScript((m) => { try { localStorage.setItem('saga-lang', 'de'); if (m) localStorage.setItem('saga-motion', m); } catch (e) { /* egal */ } }, stored);
      const p = await ctx.newPage();
      await p.goto(base, { waitUntil: 'load' });
      const r = await p.evaluate(() => ({ m: document.documentElement.classList.contains('motion'), pr: document.querySelector('.mot').getAttribute('aria-pressed'),
        anim: document.getAnimations().length, op: getComputedStyle(document.querySelector('#neu .rv')).opacity }));
      ok(r.m === want && r.pr === String(want) && (want ? r.anim > 0 : r.anim === 0 && r.op === '1'), bn + ': reduced-motion, Wahl ' + stored, r);
      await ctx.close();
    }
    // Promovideo: mit MP4 spielt es nach dem Klick (Zeit laeuft, 1920 breit); ohne MP4 bleibt das Poster mit Link stehen
    if (!NUR) {
      const da = fs.existsSync(path.join(DIR, 'video', 'saga-promo.mp4'));
      const ctx = await br.newContext({ viewport: { width: 1440, height: 900 } });
      await ctx.addInitScript(() => { try { localStorage.setItem('saga-lang', 'de'); } catch (e) { /* egal */ } });
      const p = await ctx.newPage();
      const perr = [], bad = []; p.on('pageerror', e => perr.push(String(e)));
      p.on('response', r => { if (r.status() >= 400 && /\.mp4/.test(r.url())) bad.push(r.status() + ' ' + r.url()); });
      await p.goto(base, { waitUntil: 'load' });
      await p.evaluate(() => document.querySelector('.promo').scrollIntoView());
      await p.click('.promo video', { position: { x: 30, y: 30 } }).catch(() => {});
      await p.evaluate(() => { const v = document.querySelector('.promo video'); if (v) { v.muted = true; v.play().catch(() => {}); } });
      await p.waitForTimeout(3500);
      const s = await p.evaluate(() => { const v = document.querySelector('.promo video');
        return { video: !!v, t: v ? v.currentTime : -1, w: v ? v.videoWidth : 0, err: v && v.error ? v.error.code : 0, dur: v ? v.duration : 0,
          img: (document.querySelector('.promo .frame img') || {}).naturalWidth || 0, alt: (document.querySelector('.promo .soon') || {}).textContent || '' }; });
      if (da) ok(s.video && s.t > 1 && s.w === 1920 && s.dur > 50 && !s.err && !perr.length && !bad.length, bn + ': Promovideo spielt', { s, perr, bad });
      else ok(!s.video && s.img > 0 && s.alt === 'Video als MP4 öffnen' && !perr.length, bn + ': Video fehlt -> Poster als Bild', { s, perr });
      console.log(bn, 'Video', JSON.stringify(s));
      await ctx.close();
    }
    await br.close();
  }
  srv.close();
  console.log(NUR ? 'Bilder fertig' : (fails ? `FEHLER ${fails} von ${n}` : `ALLES OK (${n} Pruefungen)`));
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
