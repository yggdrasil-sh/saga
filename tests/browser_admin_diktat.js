// Browser-Reihe Admin -> KI-Assistent -> Diktat (Saga 2.6, Bugs-DB 2274 K-F). Laeuft aus tests/kollegin_diktat.py:
//   SAGA_ADMIN=http://127.0.0.1:<port> SAGA_ADMIN_COOKIE=<hk_admin> SHOTS=<ordner> node tests/browser_admin_diktat.js
// Geprueft: Seite laedt ohne Konsolenfehler, Abschnitt "Diktat" mit Proxy-Hinweis, Schluessel nie im Feld, Hinweis wie die
// Personen ihn sehen, Verbindungstest zeigt WAV/Ogg, Handy-Breite ohne waagrechtes Scrollen. Bildschirmfotos nach SHOTS:
// admin-diktat-vorher.png (Abschnitt ausgeblendet = Stand vor K-F), admin-diktat-nachher.png, admin-diktat-test.png,
// admin-diktat-dunkel.png, admin-diktat-handy.png.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path');
const E = process.env, BASE = E.SAGA_ADMIN, SHOTS = E.SHOTS || '/tmp/kollegin-diktat-shots';
if (!BASE || !E.SAGA_ADMIN_COOKIE) { console.error('SAGA_ADMIN, SAGA_ADMIN_COOKIE setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 800))); }
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true, args: ['--no-proxy-server'] });
  const u = new URL(BASE);
  async function page(o) {
    const ctx = await br.newContext({ viewport: o.mobile ? { width: 390, height: 844 } : { width: 1280, height: 900 }, deviceScaleFactor: o.mobile ? 2 : 1,
      isMobile: !!o.mobile, hasTouch: !!o.mobile, colorScheme: o.scheme || 'light', locale: 'de', extraHTTPHeaders: { 'Accept-Language': 'de' } });
    await ctx.addCookies([{ name: 'hk_admin', value: E.SAGA_ADMIN_COOKIE, domain: u.hostname, path: '/' }]);
    const p = await ctx.newPage();
    p.__errs = [];
    p.on('console', m => { if (m.type() === 'error') p.__errs.push(m.text()); });
    p.on('pageerror', e => p.__errs.push(String(e)));
    const r = await p.goto(BASE + '/settings/ai', { waitUntil: 'load' });
    p.__status = r.status();
    return p;
  }
  // Ausschnitt: Ende der Embeddings bis Anfang der Auftraege (vorher/nachher vergleichbar)
  async function shot(p, name, hide) {
    if (hide) await p.addStyleTag({ content: '#diktat{display:none!important}' });
    await p.evaluate(() => { const e = document.getElementById('embeddings'); if (e) e.scrollIntoView({ block: 'start' }); });
    await sleep(200);
    const box = await p.evaluate(() => {
      const a = document.getElementById('embeddings'), b = document.getElementById('auftraege');
      if (!a || !b) return null;
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      return { x: 0, y: Math.max(0, ra.bottom + window.scrollY - 160), w: document.documentElement.clientWidth, h: rb.top + window.scrollY + 140 - Math.max(0, ra.bottom + window.scrollY - 160) };
    });
    if (box) await p.screenshot({ path: path.join(SHOTS, name), fullPage: true, clip: { x: box.x, y: box.y, width: box.w, height: Math.min(box.h, 3000) } });
    else await p.screenshot({ path: path.join(SHOTS, name), fullPage: true });
  }

  let p = await page({});
  ok(`Seite laedt ohne Konsolenfehler (${p.__status})`, p.__status === 200 && !p.__errs.length, p.__errs);
  const info = await p.evaluate(() => {
    const f = document.getElementById('diktat');
    if (!f) return null;
    return { h3: (f.querySelector('h3') || {}).textContent, text: f.textContent, key: f.querySelector('[name=api_key]').value,
             url: f.querySelector('[name=url]').value, where: document.getElementById('stt-where').textContent,
             formats: document.getElementById('stt-formats').textContent };
  });
  ok('Abschnitt Diktat mit Proxy-Hinweis und Cloud-Schalter', info && info.h3 === 'Diktat' && info.text.includes('proxy_request_buffering off')
     && info.text.includes('Cloud erlauben'), info);
  ok('Schluessel steht nie im Feld, Hinweis wie die Personen ihn sehen', info && info.key === '' && /im Heimnetz/.test(info.where)
     && /OGG, WAV/.test(info.formats), info);
  await shot(p, 'admin-diktat-vorher.png', true);
  await p.reload({ waitUntil: 'load' });
  await shot(p, 'admin-diktat-nachher.png', false);
  await p.click('#stt-test');
  await p.waitForFunction(() => { const o = document.getElementById('stt-out'); return o && !o.hidden && /WAV|✗/.test(o.textContent); }, null, { timeout: 30000 }).catch(() => {});
  const out = await p.textContent('#stt-out');
  ok(`Verbindungstest zeigt WAV und OGG angenommen ("${out.replace(/\n/g, ' | ').slice(0, 160)}")`, /✓ WAV/.test(out) && /✓ OGG/.test(out), out);
  await p.evaluate(() => document.getElementById('diktat').scrollIntoView({ block: 'start' }));
  await sleep(200);
  await (await p.$('#diktat')).screenshot({ path: path.join(SHOTS, 'admin-diktat-test.png') });
  ok('keine Konsolenfehler nach dem Test', !p.__errs.length, p.__errs);
  await p.context().close();

  p = await page({ scheme: 'dark' });
  await p.evaluate(() => document.getElementById('diktat').scrollIntoView({ block: 'start' }));
  await sleep(200);
  await (await p.$('#diktat')).screenshot({ path: path.join(SHOTS, 'admin-diktat-dunkel.png') });
  await p.context().close();

  p = await page({ mobile: true });
  const wide = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  ok(`Handy-Breite ohne waagrechtes Scrollen (${wide} px)`, wide <= 1, wide);
  await p.evaluate(() => document.getElementById('diktat').scrollIntoView({ block: 'start' }));
  await sleep(200);
  await (await p.$('#diktat')).screenshot({ path: path.join(SHOTS, 'admin-diktat-handy.png') });
  await p.context().close();
  await br.close();
  console.log(`\nAdmin Diktat ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
