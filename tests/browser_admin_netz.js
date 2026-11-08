// Browser-Reihe Admin -> Netz (Saga 2.5, Bug-DB 1641). Laeuft aus tests/admin_netz.py (Testdaten: Partner B, Knoten-Attrappe,
// Knoten des Partners, Clients je Grobklasse, laufender Anruf ueber den Knoten):
//   SAGA_ADMIN=http://127.0.0.1:8394 SAGA_ADMIN_COOKIE=<hk_admin> NET_PEER=<host> NET_NODE=<name> SHOTS=<ordner> node tests/browser_admin_netz.js
// Geprueft: Seite laedt ohne Konsolen-/CSP-Fehler, Canvas zeichnet (Pixel), Karte kennt Server/Partner/Knoten/Clients,
// Tabellenansicht (Knoten, Verbindungen, Anrufe) mit denselben Daten, Live-Kanal verbunden, Lichtpunkte des Anrufs laufen
// (Bilder zaehlen hoch), Ueberfahren und Tastatur zeigen die Detailkarte, hell/dunkel, Handy-Breite ohne waagrechtes
// Scrollen, prefers-reduced-motion (keine Animation, nur Zustaende), versteckter Tab pausiert. Bildschirmfotos nach SHOTS.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path');
const E = process.env, BASE = E.SAGA_ADMIN, SHOTS = E.SHOTS || '/tmp/admin-netz-shots';
if (!BASE || !E.SAGA_ADMIN_COOKIE) { console.error('SAGA_ADMIN, SAGA_ADMIN_COOKIE setzen'); process.exit(2); }
const PEER = E.NET_PEER || '', NODE = E.NET_NODE || '';
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 800))); }
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true, args: ['--no-proxy-server'] });
  const u = new URL(BASE);
  async function page(o) {
    const ctx = await br.newContext({ viewport: o.mobile ? { width: 390, height: 844 } : { width: 1280, height: 900 }, deviceScaleFactor: o.mobile ? 2 : 1,
      isMobile: !!o.mobile, hasTouch: !!o.mobile, colorScheme: o.scheme || 'light', reducedMotion: o.rm ? 'reduce' : 'no-preference',
      locale: o.lang || 'de', extraHTTPHeaders: { 'Accept-Language': o.lang || 'de' } });
    await ctx.addCookies([{ name: 'hk_admin', value: E.SAGA_ADMIN_COOKIE, domain: u.hostname, path: '/' }]);
    const p = await ctx.newPage();
    p.__errs = [];
    p.on('console', m => { if (m.type() === 'error') p.__errs.push(m.text()); });
    p.on('pageerror', e => p.__errs.push(String(e)));
    const r = await p.goto(BASE + '/net', { waitUntil: 'load' });
    p.__status = r.status();
    return p;
  }
  const stats = p => p.evaluate(() => window.SagaNet && window.SagaNet.stats());
  const painted = p => p.evaluate(() => {
    const c = document.getElementById('net-canvas'), x = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let n = 0; for (let i = 3; i < x.length; i += 16) if (x[i] > 0) n++;
    return n;
  });

  // --- hell, Desktop ---------------------------------------------------------------------------------------------------
  let p = await page({ scheme: 'light' });
  await sleep(2500);
  let st = await stats(p);
  const kinds = (st && st.nodes || []).join('|');
  ok(`hell: Seite laedt ohne Konsolenfehler (${p.__status})`, p.__status === 200 && !p.__errs.length, p.__errs);
  ok(`Karte: Server, Partner, Knoten, Partner-Knoten, Clients (${kinds})`, /self:/.test(kinds) && kinds.includes('peer:') && kinds.includes('horn:' + NODE)
     && kinds.includes('remote:horn.partner.example') && kinds.includes('client:Android-App') && kinds.includes('client:Browser · Windows'), st);
  ok(`Canvas zeichnet (${await painted(p)} Pixel)`, (await painted(p)) > 500);
  ok(`Live-Kanal verbunden ("${await p.textContent('#net-live')}")`, await p.evaluate(() => document.getElementById('net-live').classList.contains('on')));
  const f0 = st.frames; await sleep(1000); const st1 = await stats(p);
  ok(`Anruf: Lichtpunkte laufen (Punkte ${st1.dots}, Bilder ${f0} -> ${st1.frames})`, st1.calls === 1 && st1.dots >= 2 && st1.frames - f0 >= 20 && st1.anim);
  const nodesTxt = await p.textContent('#net-nodes');
  ok('Tabellenansicht: Server, Partner, Knoten, Partner-Knoten, Clients', nodesTxt.includes(PEER) && nodesTxt.includes(NODE) && nodesTxt.includes('horn.partner.example')
     && nodesTxt.includes('Android-App') && nodesTxt.includes('iOS-App') && /Latenz \d+ ms/.test(nodesTxt));
  const edgesTxt = await p.textContent('#net-edges'), callsTxt = await p.textContent('#net-calls');
  ok('Tabellenansicht: Verbindungen (Foederation, Server ↔ Knoten, Clients ↔ Server) und Anruf ueber den Knoten',
     edgesTxt.includes('Föderation') && edgesTxt.includes('Server ↔ Knoten') && edgesTxt.includes('Clients ↔ Server') && callsTxt.includes('über Knoten ' + NODE));
  const th = await p.$$eval('#net-nodes tbody th[scope=row]', x => x.length);
  ok(`Tabellen: Zeilenkoepfe (th scope=row: ${th}), Canvas mit role=img und Beschreibung`, th >= 5
     && await p.evaluate(() => { const c = document.getElementById('net-canvas'); return c.getAttribute('role') === 'img' && !!c.getAttribute('aria-label'); }));
  // Ueberfahren: dieser Server liegt in der Mitte
  const bb = await (await p.$('#net-canvas')).boundingBox();
  const sp = await p.evaluate(() => window.SagaNet.pos('self'));
  await p.mouse.move(bb.x + sp[0], bb.y + sp[1]);
  await sleep(300);
  const tip = await p.evaluate(() => { const t = document.getElementById('net-tip'); return { hidden: t.hidden, txt: t.textContent }; });
  ok(`Ueberfahren: Detailkarte des Servers ("${tip.txt.slice(0, 60)}")`, !tip.hidden && tip.txt.includes('Dieser Server') && tip.txt.includes('Version'));
  await p.screenshot({ path: path.join(SHOTS, 'netz-hell.png') });
  await p.mouse.move(bb.x + 5, bb.y + bb.height - 5);
  // Tastatur
  await p.focus('#net-canvas');
  await p.keyboard.press('ArrowRight'); await sleep(150); await p.keyboard.press('ArrowRight'); await sleep(150);
  const tip2 = await p.evaluate(() => { const t = document.getElementById('net-tip'); return { hidden: t.hidden, txt: t.textContent }; });
  ok(`Tastatur: Pfeiltasten waehlen Knoten, Detailkarte ("${tip2.txt.slice(0, 50)}")`, !tip2.hidden && tip2.txt.length > 5);
  await p.keyboard.press('Escape'); await sleep(100);
  ok('Tastatur: Esc schliesst die Detailkarte', await p.evaluate(() => document.getElementById('net-tip').hidden));
  // versteckter Tab: Animation pausiert, Live-Kanal zu
  await p.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); });
  const h0 = (await stats(p)).frames; await sleep(800); const h1 = await stats(p);
  ok(`versteckter Tab: keine Bilder (${h0} -> ${h1.frames}), Aktualisierung pausiert`, h1.frames - h0 <= 2 && !h1.anim
     && (await p.textContent('#net-live')).includes('pausiert'));
  await p.context().close();

  // --- dunkel, Desktop -------------------------------------------------------------------------------------------------
  p = await page({ scheme: 'dark' });
  await sleep(2500);
  const bg = await p.evaluate(() => getComputedStyle(document.body).backgroundColor);
  ok(`dunkel: Seite ohne Konsolenfehler, dunkles Farbschema (${bg})`, p.__status === 200 && !p.__errs.length && bg === 'rgb(15, 20, 28)', p.__errs);
  // Ein Knoten angetippt (ausgewaehlt) fuer das Foto: Partner
  const pos = await p.evaluate(() => { const s = window.SagaNet.stats(); return s; });
  await p.screenshot({ path: path.join(SHOTS, 'netz-dunkel.png') });
  await p.screenshot({ path: path.join(SHOTS, 'netz-dunkel-ganz.png'), fullPage: true });
  ok('dunkel: Karte mit allen Knoten', pos.nodes.length >= 7);
  await p.context().close();

  // --- reduced motion --------------------------------------------------------------------------------------------------
  p = await page({ rm: true });
  await sleep(1500);
  const r0 = await stats(p); await sleep(1000); const r1 = await stats(p);
  ok(`prefers-reduced-motion: keine Animation (Bilder ${r0.frames} -> ${r1.frames}), Anruf als ruhender Punkt (${r1.dots})`,
     !r1.anim && r1.frames === r0.frames && r1.dots >= 1 && !p.__errs.length, { r0, r1, errs: p.__errs });
  ok(`prefers-reduced-motion: Canvas zeichnet trotzdem (${await painted(p)} Pixel)`, (await painted(p)) > 500);
  await p.context().close();

  // --- Handy-Breite, Thai --------------------------------------------------------------------------------------------
  for (const lang of ['de', 'th']) {
    p = await page({ mobile: true, lang });
    await sleep(1200);
    const ow = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    ok(`Handy (${lang}): kein waagrechtes Scrollen (${ow}px), keine Konsolenfehler`, ow <= 1 && !p.__errs.length, p.__errs);
    if (lang === 'de') await p.screenshot({ path: path.join(SHOTS, 'netz-handy.png'), fullPage: true });
    await p.context().close();
  }

  await br.close();
  console.log(`\nBROWSER-ADMIN-NETZ ${res.filter(Boolean).length}/${res.length} OK – ` + (res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
