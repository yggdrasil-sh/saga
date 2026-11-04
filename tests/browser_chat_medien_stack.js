// Browser-Ende-zu-Ende fuer Chat-Medien (2.5 medien1, static/chat.js) gegen einen laufenden Server - drei Playwright-
// Kontexte (drei Konten) im headless Chromium mit unechtem Mikrofon:
//   SAGA_BASE=... SAGA_USER=konto-a SAGA_PASS=... SAGA_USER2=konto-b SAGA_PASS2=... SAGA_USER3=konto-c SAGA_PASS3=...
//   [BIG_MB=11] [PLAYWRIGHT=...] node tests/browser_chat_medien_stack.js
// Wegwerf-Konten ohne Chats miteinander (der Test legt die Direkt-Chats A-B und A-C an); BIG_MB muss ueber ATTACH_MAX_MB
// des Servers liegen (Standard 10), damit die grosse Datei als saga-file/v2 geht.
// Geprueft: A schickt ein Bild ohne Text; B antwortet ueber das Nachrichtenmenue -> Antwortleiste mit Vorschaubild und
// "Foto", das Zitat in der Antwort ebenso (bei A und B); Bildansicht blaettert zwischen zwei Bildern ("1 / 2");
// "Weiterleiten" im Menue einer einzelnen Nachricht: Bild (v1) an C, grosse Datei (saga-file/v2, BIG_MB, ueber
// ATTACH_MAX_MB) an C (neu hochgeladen in Teilen, bei C gleiche Bytes), Sprachnachricht an C (Wellenform, spielt ab),
// kein "[Anhang nicht weitergeleitet]"; keine Seitenfehler. Kein waitForFunction (CSP ohne eval).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os'), crypto = require('crypto');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2 || !E.SAGA_USER3 || !E.SAGA_PASS3) {
  console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2, SAGA_USER3/PASS3 setzen'); process.exit(2);
}
const BIG = (+E.BIG_MB || 11) * 1048576;
const res = [], errs = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const sha = b => crypto.createHash('sha256').update(b).digest('hex');

async function session(browser, user, pass) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE', acceptDownloads: true });
  const p = await ctx.newPage();
  p.on('pageerror', e => { errs.push(user + ': ' + e.message); console.log('  [pageerror ' + user + '] ' + e.message); });
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', pass);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  return p;
}
const RKF = u => path.join(os.tmpdir(), 'saga-browser-rk-' + BASE.replace(/\W+/g, '_') + '-' + u + '.txt');
async function chatKeys(q, user) {
  await q.goto(BASE + '/chats'); await q.waitForTimeout(2500);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim(); fs.writeFileSync(RKF(user), rk, { mode: 0o600 });
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 60000 });
  } else if (await q.$('.ct-rk-go') && fs.existsSync(RKF(user))) {
    await q.click('.ct-rk-go'); await q.fill('.ct-rk-in', fs.readFileSync(RKF(user), 'utf8').trim());
    await q.click('.ct-rk-ok'); await q.waitForSelector('.ct-rk-ok', { state: 'detached', timeout: 60000 });
  }
  await q.waitForTimeout(800);
}
const rows = p => p.evaluate(() => [...document.querySelectorAll('#ct-msgs .ct-msg')].map(r => ({
  id: r.dataset.id, t: (r.querySelector('.ct-text') || {}).textContent || '', img: !!r.querySelector('.ct-img'),
  file: (r.querySelector('.ct-file-n') || {}).textContent || '', vm: !!r.querySelector('.vm'), mine: r.classList.contains('mine') })));
const rowSel = id => '#ct-msgs .ct-msg[data-id="' + id + '"]';
async function menuOf(p, id) {
  await p.hover(rowSel(id)); await p.click(rowSel(id) + ' .ct-act:not(.ct-react-b)'); await p.waitForSelector('.menu .menu-item');
}
async function openDm(p, user) {
  await p.goto(BASE + '/chats#u' + encodeURIComponent(user)); await p.waitForSelector('#ct-chat:not([hidden])'); await p.waitForTimeout(1200);
}
async function sendFile(p, file, pred) {         // pred(row): woran die neue eigene Nachricht zu erkennen ist
  const seen = new Set((await rows(p)).map(x => x.id));
  await p.setInputFiles('#ct-file', file); await p.waitForTimeout(400);
  await p.click('#ct-send');
  return until(async () => (await rows(p)).filter(x => x.mine && !seen.has(x.id) && pred(x)).pop(), 90000);
}
// Einzelne Nachricht ueber ihr Menue in den Chat "who" weiterleiten; liefert Menue, Rueckfrage und Toast
async function forward(p, id, who) {     // who: Name des Ziel-Chats in der Liste
  await menuOf(p, id);
  const items = await p.$$eval('.menu .menu-item', x => x.map(i => i.textContent));
  await p.click('.menu .menu-item:has-text("Weiterleiten")');
  await p.waitForSelector('.ct-fwd-q'); await p.fill('.ct-fwd-q', who);
  await until(async () => (await p.$$('.ct-fwd-item')).length === 1, 5000);
  await p.click('.ct-fwd-item');
  await p.waitForSelector('.hk-ask'); const ask = await p.textContent('.hk-ask');
  await p.click('.hk-ask .btn-primary');
  const toast = await until(() => p.evaluate(() => { const t = [...document.querySelectorAll('.toast')].map(x => x.textContent).join(' | ');
    return /weitergeleitet\./.test(t) ? t : null; }), 120000, 400);
  return { items, ask, toast };
}

(async () => {
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2, U3 = E.SAGA_USER3;
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2), c = await session(browser, U3, E.SAGA_PASS3);
    await chatKeys(a, U1); await chatKeys(b, U2); await chatKeys(c, U3);
    ok('Chat-Schluessel in drei Browsern bereit', !(await a.$('.ct-ov')) && !(await b.$('.ct-ov')) && !(await c.$('.ct-ov')));
    // Ziel-Chat A-C anlegen (eine Textnachricht), dann A-B
    await openDm(a, U3); await a.fill('#ct-input', 'Hallo C'); await a.press('#ct-input', 'Enter'); await a.waitForTimeout(1000);
    const toC = (await a.textContent('#ct-title')).trim();   // Stufe 3: Name aus dem Profil, sonst "Unbekannte Person (…)"
    await openDm(a, U2);
    const png = Buffer.from(await a.evaluate(() => {
      const cv = document.createElement('canvas'); cv.width = 800; cv.height = 600; const g = cv.getContext('2d');
      for (let i = 0; i < 20; i++) { g.fillStyle = 'hsl(' + i * 18 + ',70%,50%)'; g.fillRect(i * 40, 0, 40, 600); }
      return cv.toDataURL('image/png').split(',')[1];
    }), 'base64');
    const m1 = await sendFile(a, { name: 'farben.png', mimeType: 'image/png', buffer: png }, x => x.img);
    ok('A: Bild ohne Text gesendet', m1 && m1.img && !m1.t, m1);

    // --- Antwort mit Zitat (Vorschaubild + "Foto") ------------------------------------------------------------------
    await b.goto(BASE + '/chats'); await b.waitForTimeout(1200); await b.click('#ct-list .ct-item-b'); await b.waitForSelector('#ct-chat:not([hidden])');
    const bImg = await until(async () => (await rows(b)).find(r => r.img), 20000);
    await menuOf(b, bImg.id); await b.click('.menu .menu-item:has-text("Antworten")');
    const bar = await until(() => b.evaluate(() => {
      const r = document.getElementById('ct-reply'), th = r && r.querySelector('img.ct-q-th');
      return r && !r.hidden && th && th.complete && th.naturalWidth > 0 ? { t: r.textContent, w: th.naturalWidth } : null;
    }), 15000);
    ok('B: Antwortleiste mit Vorschaubild und "Foto"', bar && /Foto/.test(bar.t), bar);
    await b.fill('#ct-input', 'Schoenes Bild'); await b.press('#ct-input', 'Enter');
    const quoteIn = p => until(() => p.evaluate(() => {
      const r = [...document.querySelectorAll('#ct-msgs .ct-msg')].find(x => (x.querySelector('.ct-text') || {}).textContent === 'Schoenes Bild');
      const q = r && r.querySelector('.ct-quote.ct-q-media'), th = q && q.querySelector('img.ct-q-th');
      return q && th && th.complete && th.naturalWidth > 0 ? { t: q.textContent, w: th.naturalWidth } : null;
    }), 20000);
    const qb = await quoteIn(b);
    ok('B: Zitat in der Antwort mit Vorschaubild und "Foto"', qb && /Foto/.test(qb.t), qb);
    const qa = await quoteIn(a);
    ok('A: Zitat live mit Vorschaubild und "Foto"', qa && /Foto/.test(qa.t), qa);

    // --- Bildansicht: zweites Bild, blaettern -----------------------------------------------------------------------
    const m2 = await sendFile(a, { name: 'zwei.png', mimeType: 'image/png', buffer: png }, x => x.img);
    await a.locator(rowSel(m1.id) + ' .ct-img').click(); await a.waitForSelector('.ct-gal img');
    const g1 = await until(() => a.evaluate(() => { const n = document.querySelector('.ct-gal-n'), i = document.querySelector('.ct-gal img');
      return n && i && i.naturalWidth === 800 ? n.textContent : null; }), 15000);
    await a.keyboard.press('ArrowRight');
    const g2 = await until(() => a.evaluate(() => { const n = document.querySelector('.ct-gal-n'); return n && n.textContent === '2 / 2' ? n.textContent : null; }), 5000);
    ok('Bildansicht: Original geladen, "1 / 2" -> Pfeil rechts "2 / 2"', g1 === '1 / 2' && g2 === '2 / 2', { g1, g2 });
    await a.keyboard.press('Escape'); await a.waitForTimeout(300);
    ok('Bildansicht: Esc schliesst', !(await a.$('.ct-gal')));

    // --- Weiterleiten: Bild (v1) --------------------------------------------------------------------------------------
    const f1 = await forward(a, m2.id, toC);
    ok('Menue einer Nachricht hat "Weiterleiten"', f1.items.some(t => /Weiterleiten/.test(t)), f1.items);
    ok('Bild weitergeleitet: Rueckfrage + Toast ohne "nicht weitergeleitet"', /weiterleiten\?/.test(f1.ask) && /1 Nachricht weitergeleitet\./.test(f1.toast)
       && !/nicht weitergeleitet/.test(f1.toast), f1);

    // --- Weiterleiten: grosse Datei (saga-file/v2) ---------------------------------------------------------------------
    const big = crypto.randomBytes(BIG);
    const v1Ups = [], v2Puts = [];
    a.on('request', r => {
      if (r.method() === 'POST' && /\/api\/chats\/\d+\/files$/.test(r.url())) v1Ups.push(r.url());
      if (r.method() === 'PUT' && /\/api\/chat\/uploads\//.test(r.url())) v2Puts.push(r.url());
    });
    const m3 = await sendFile(a, { name: 'gross.bin', mimeType: 'application/octet-stream', buffer: big }, x => x.file === 'gross.bin');
    ok('A: grosse Datei gesendet als saga-file/v2 (' + (BIG >> 20) + ' MB, ' + v2Puts.length + ' PUT, ' + v1Ups.length + ' v1)',
       m3 && m3.file === 'gross.bin' && v2Puts.length > 0 && v1Ups.length === 0, { m3, puts: v2Puts.length, v1: v1Ups.length });
    v1Ups.length = 0; v2Puts.length = 0;
    const f2 = await forward(a, m3.id, toC);
    ok('Grosse Datei weitergeleitet (neu hochgeladen als v2), Toast ohne "nicht weitergeleitet"',
       /1 Nachricht weitergeleitet\./.test(f2.toast) && !/nicht weitergeleitet/.test(f2.toast) && v2Puts.length > 0 && v1Ups.length === 0,
       { toast: f2.toast, puts: v2Puts.length, v1: v1Ups.length });

    // --- Weiterleiten: Sprachnachricht --------------------------------------------------------------------------------
    await a.reload(); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);
    let voiceOk = false;
    if (await a.isVisible('#ct-mic')) {
      await a.click('#ct-mic'); await a.waitForSelector('.ct-rec-stop:not([disabled])', { timeout: 15000 });
      await a.waitForTimeout(2500); await a.click('.ct-rec-stop');
      await a.waitForSelector('.ct-rec .vm', { timeout: 10000 }); await a.click('.ct-rec-send');
      const mv = await until(async () => (await rows(a)).filter(r => r.vm && r.mine).pop(), 30000);
      ok('A: Sprachnachricht gesendet', !!mv, mv);
      if (mv) {
        const f3 = await forward(a, mv.id, toC);
        ok('Sprachnachricht weitergeleitet, Toast ohne "nicht weitergeleitet"', /1 Nachricht weitergeleitet\./.test(f3.toast) && !/nicht weitergeleitet/.test(f3.toast), f3.toast);
        voiceOk = true;
      }
    } else console.log('--   Sprachnachricht: kein Mikrofon-Knopf (Funktion aus) - uebersprungen');

    // --- C sieht alles ------------------------------------------------------------------------------------------------
    await c.goto(BASE + '/chats'); await c.waitForTimeout(1500); await c.click('#ct-list .ct-item-b'); await c.waitForSelector('#ct-chat:not([hidden])');
    const cr = await until(async () => { const r = await rows(c); return r.some(x => x.img) && r.some(x => x.file === 'gross.bin') && (!voiceOk || r.some(x => x.vm)) ? r : null; }, 30000);
    ok('C: Bild, grosse Datei' + (voiceOk ? ', Sprachnachricht' : '') + ' angekommen, kein "[Anhang nicht weitergeleitet]"',
       cr && !cr.some(x => /nicht weitergeleitet/.test(x.t)), cr || await rows(c));
    const cImg = await until(() => c.evaluate(() => { const i = [...document.querySelectorAll('#ct-msgs .ct-img img')].pop(); return i && i.complete && i.naturalWidth > 0 ? i.naturalWidth : null; }), 15000);
    ok('C: weitergeleitetes Bild laesst sich entschluesseln', !!cImg, cImg);
    await c.evaluate(() => { delete window.showSaveFilePicker; window.showSaveFilePicker = undefined; });
    const [dl] = await Promise.all([c.waitForEvent('download', { timeout: 90000 }),
      c.locator('#ct-msgs .ct-file', { hasText: 'gross.bin' }).last().click()]);
    const out = path.join(os.tmpdir(), 'saga-medien-dl-' + Date.now()); await dl.saveAs(out);
    const got = fs.readFileSync(out); fs.unlinkSync(out);
    ok('C: weitergeleitete grosse Datei = Original (gleiche Bytes, Dateiname)', got.length === big.length && sha(got) === sha(big) && dl.suggestedFilename() === 'gross.bin',
       { len: got.length, want: big.length, n: dl.suggestedFilename() });
    if (voiceOk) {
      const v = await until(() => c.evaluate(() => { const x = [...document.querySelectorAll('#ct-msgs .vm')].pop();
        return x ? { bars: x.querySelectorAll('.vm-wave span').length, time: x.querySelector('.vm-time').textContent } : null; }), 15000);
      ok('C: weitergeleitete Sprachnachricht mit Wellenform (64 Balken) und Dauer', v && v.bars === 64 && /^0:0[1-4]$/.test(v.time), v);
      await c.locator('#ct-msgs .vm-play').last().click();
      const ps = await until(() => c.evaluate(() => { const x = [...document.querySelectorAll('#ct-msgs .vm')].pop(), s = x && x.vmState();
        return s && !s.paused && s.t > 0.3 ? s : null; }), 15000);
      ok('C: weitergeleitete Sprachnachricht spielt ab', !!ps, ps);
    }
    ok('Keine Seitenfehler', errs.length === 0, errs);
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e));
  } finally {
    await browser.close();
  }
  const n = res.filter(Boolean).length;
  console.log('\nBROWSER-CHAT-MEDIEN-STACK ' + n + '/' + res.length + (n === res.length ? ' OK – ALLES GRUEN' : ' - FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
