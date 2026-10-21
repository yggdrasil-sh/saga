// Browser-Test der Admin-Seite "Anrufe" (Saga 2.5, Gjallarhorn P8, Konzept v2 §4.1). Laeuft aus tests/horn_erweitern.py:
//   SAGA_ADMIN=http://127.0.0.1:8073 SAGA_ADMIN_COOKIE=<hk_admin> SHOTS=<ordner> [PLAYWRIGHT=...] node tests/browser_horn_admin.js
// Geprueft: alle Abschnitte sichtbar, keine CSP-/Konsolenfehler, Handy-Breite ohne waagrechtes Scrollen, hell und dunkel,
// Dialog "Erweitern" mit Skript und Live-Fortschritt (Warte auf Knoten ...), Kopieren, Port-Pruefung ohne Neuladen,
// alle 5 Sprachen. Bildschirmfotos nach SHOTS (calls-<sprache>-<hell|dunkel>-<desktop|handy>.png).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path');
const E = process.env, BASE = E.SAGA_ADMIN, SHOTS = E.SHOTS || '/tmp';
if (!BASE || !E.SAGA_ADMIN_COOKIE) { console.error('SAGA_ADMIN, SAGA_ADMIN_COOKIE setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 800))); }

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true, args: ['--no-proxy-server'] });
  const u = new URL(BASE);
  async function page(lang, scheme, mobile) {
    const ctx = await br.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 900 }, deviceScaleFactor: mobile ? 2 : 1,
      isMobile: !!mobile, hasTouch: !!mobile, colorScheme: scheme, locale: lang, extraHTTPHeaders: { 'Accept-Language': lang } });
    await ctx.addCookies([{ name: 'hk_admin', value: E.SAGA_ADMIN_COOKIE, domain: u.hostname, path: '/' }]);
    await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE });
    const p = await ctx.newPage();
    p.__errs = [];
    p.on('console', m => { if (m.type() === 'error') p.__errs.push(m.text()); });
    p.on('pageerror', e => p.__errs.push(String(e)));
    return p;
  }
  // Sprache per Konto-Cookie des Admin-Bereichs (Sprachumschalter) - hier reicht Accept-Language
  const LANGS = [['de', 'Gjallarhorn – große Gruppenanrufe'], ['en', 'Gjallarhorn'], ['es', 'Gjallarhorn'], ['ru', 'Gjallarhorn'], ['th', 'Gjallarhorn']];
  for (const [lang, h] of LANGS) {
    for (const scheme of (lang === 'de' ? ['light', 'dark'] : ['light'])) {
      for (const mobile of (lang === 'de' || lang === 'th' ? [false, true] : [false])) {
        const p = await page(lang, scheme, mobile);
        const r = await p.goto(BASE + '/calls', { waitUntil: 'load' });
        const title = await p.textContent('#horn h3');
        const ow = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        const tag = `${lang}-${scheme === 'dark' ? 'dunkel' : 'hell'}-${mobile ? 'handy' : 'desktop'}`;
        await p.screenshot({ path: path.join(SHOTS, `calls-${tag}.png`), fullPage: true });
        ok(`${tag}: Seite laedt, Titel, keine Konsolenfehler, kein waagrechtes Scrollen (${r.status()}, ${ow}px)`,
          r.status() === 200 && title.includes(h) && !p.__errs.length && ow <= 1, { errs: p.__errs, ow });
        if (scheme === 'dark') {
          const bg = await p.evaluate(() => getComputedStyle(document.body).backgroundColor);
          ok(`${tag}: dunkles Farbschema (${bg})`, bg === 'rgb(15, 20, 28)');
        }
        await p.context().close();
      }
    }
  }
  // Erweitern mit Live-Fortschritt
  const p = await page('de', 'light', false);
  await p.goto(BASE + '/calls', { waitUntil: 'load' });
  await p.click('.horn-ext > summary');
  await p.fill('.horn-pair-f input[name=name]', 'horn-9');
  await p.click('.horn-pair-f button[type=submit]');
  await p.waitForSelector('.horn-pair-out:not([hidden])', { timeout: 10000 });
  const script = await p.textContent('.horn-script');
  const prog = await p.textContent('.horn-prog li[data-k="wait"]');
  const exp = await p.textContent('.horn-exp');
  ok(`Erweitern: Skript mit Code und Name, Fortschritt "Warte auf Knoten", Ablaufzeit (${exp})`, /HORN_PAIR=hkh_[A-Za-z0-9_-]{43}/.test(script)
    && script.includes('HORN_NAME=horn-9.example.org') && prog.includes('Warte auf Knoten') && /\d\d:\d\d/.test(exp));
  await p.screenshot({ path: path.join(SHOTS, 'calls-de-erweitern.png'), fullPage: true });
  await p.click('.horn-pair-out button[data-copy=".horn-script"]');
  const clip = await p.evaluate(() => navigator.clipboard.readText()).catch(() => '');
  ok('Kopieren legt das Skript in die Zwischenablage', clip === script);
  const polls = [];
  p.on('request', q => { if (q.url().includes('/api/calls/pair/')) polls.push(Date.now()); });
  await p.waitForTimeout(5000);
  ok(`Live-Fortschritt fragt hoechstens alle 2 s (${polls.length} Abfragen in 5 s)`, polls.length >= 1 && polls.length <= 4, polls.length);
  // Port pruefen ohne Neuladen (erster Knoten mit Pruefknopf)
  const btn = await p.$('button[data-check]');
  if (btn) {
    const nid = await btn.getAttribute('data-check');
    await btn.click();
    await p.waitForFunction(id => { const c = document.querySelector(`.horn-port-t[data-node="${id}"] .pc-tcp`); return c && !/Noch nicht/.test(c.textContent); }, nid, { timeout: 15000 }).catch(() => {});
    const cell = await p.textContent(`.horn-port-t[data-node="${nid}"] .pc-tcp`);
    ok(`Port pruefen: Ergebnis ohne Neuladen (${cell.trim()})`, /Erreichbar|Nicht erreichbar/.test(cell));
    await p.screenshot({ path: path.join(SHOTS, 'calls-de-portpruefung.png'), fullPage: true });
  } else ok('Port pruefen: Knopf vorhanden', false);
  ok(`keine Konsolenfehler im Ablauf (${p.__errs.length})`, !p.__errs.length, p.__errs);
  await br.close();
  console.log(`\nBROWSER-HORN-ADMIN ${res.filter(Boolean).length}/${res.length}`, res.every(Boolean) ? '- ALLES GRUEN' : '- FEHLER');
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
