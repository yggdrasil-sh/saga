// Browser-Ende-zu-Ende fuer Gelesen-Bestaetigungen und Online-Status (2.4, static/chat.js, saga/chat_status.py) - zwei
// Playwright-Kontexte (zwei Konten) im headless Chrome gegen einen Test-Stack:
//   SAGA_BASE=http://host:port SAGA_USER=konto-a SAGA_PASS=... SAGA_USER2=konto-b SAGA_PASS2=... [SHOTS=/pfad] \
//   [PLAYWRIGHT=/pfad/zu/node_modules/playwright-core] node tests/browser_gelesen.js
// Geprueft: ✓ zugestellt, ✓✓ sobald B den Chat sieht (Bestaetigung versiegelt, im Browser geoeffnet und Signatur geprueft),
// Online-Punkt in der Liste und "online" im Chatkopf, "zuletzt online" nach dem Trennen, Datenschutz-Schalter in
// "Chats & Geraete" (aus: kein ✓✓ und kein Online-Status - gegenseitig), Handy-Ansicht, keine Seitenfehler.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '';
const res = [], errs = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, name + '.png') }); } }
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

async function session(browser, user, pass, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: opts.scheme || 'dark' });
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
const lastTick = p => p.evaluate(() => { const t = [...document.querySelectorAll('#ct-msgs .ct-msg.mine .ct-tick')].pop(); return t ? { text: t.textContent, read: t.classList.contains('ct-read'), title: t.title } : null; });
async function privacy(p, which, on, shotName) {
  await p.click('#ct-settings'); await p.waitForSelector('.ct-priv-' + which + ' input');
  if (shotName) { await p.$eval('.ct-priv-' + which, e => e.scrollIntoView({ block: 'center' })); await shot(p, shotName); }
  const cur = await p.isChecked('.ct-priv-' + which + ' input');
  if (cur !== on) { await p.click('.ct-priv-' + which + ' input'); await p.waitForTimeout(800); }
  const now = await p.isChecked('.ct-priv-' + which + ' input');
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);
  return now === on;
}

(async () => {
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2);
    await chatKeys(a, U1); await chatKeys(b, U2);
    ok('Chat-Schluessel in beiden Browsern bereit', !(await a.$('.ct-ov')) && !(await b.$('.ct-ov')));
    // B ist woanders (Dashboard): A schreibt -> ✓ zugestellt, noch nicht gelesen
    await b.goto(BASE + '/'); await b.waitForTimeout(1000);
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
    await a.fill('#ct-input', 'Hallo, liest du das?'); await a.press('#ct-input', 'Enter');
    const t1 = await until(() => lastTick(a), 10000);
    ok('A: eigene Nachricht mit ✓ (zugestellt)', t1 && t1.text === '✓' && !t1.read, t1);
    ok('A: Online-Status von B im Kopf ("online", B hat den Live-Kanal offen)',
      await until(() => a.evaluate(() => !!document.querySelector('#ct-sub .ct-online')), 15000));
    ok('A: Online-Ring am Profilbild in der Liste', await a.evaluate(() => !!document.querySelector('#ct-list .ct-item .ct-av.ct-on')));
    await shot(a, 'gelesen-1-zugestellt');
    // B oeffnet den Chat -> Bestaetigung -> ✓✓ bei A
    await b.goto(BASE + '/chats'); await b.waitForTimeout(1500);
    await b.click('#ct-list .ct-item-b'); await b.waitForSelector('#ct-chat:not([hidden])'); await b.waitForTimeout(800);
    const t2 = await until(async () => { const t = await lastTick(a); return t && t.read ? t : null; }, 15000);
    ok('A: ✓✓ gelesen, sobald B den Chat sieht (versiegelt, Signatur im Browser geprueft)', t2 && t2.text === '✓✓' && /Gelesen/.test(t2.title), t2 || await lastTick(a));
    ok('B: Online-Status von A im Kopf', await until(() => b.evaluate(() => !!document.querySelector('#ct-sub .ct-online')), 15000));
    await shot(a, 'gelesen-2-gelesen');
    // B antwortet, A sieht es offen -> ✓✓ bei B
    await b.fill('#ct-input', 'Ja, gelesen!'); await b.press('#ct-input', 'Enter');
    const t3 = await until(async () => { const t = await lastTick(b); return t && t.read ? t : null; }, 15000);
    ok('B: Antwort wird bei offenem Chat von A sofort ✓✓', !!t3, await lastTick(b));
    // Gruppe: "gelesen von" (Knopf am Haken, Liste per Klick)
    const gid = await a.evaluate(u => fetch('/api/chats', { method: 'POST', credentials: 'same-origin', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'free', members: [u] }) }).then(r => r.json()).then(d => d.chat.id), U2);
    await a.goto(BASE + '/chats#c' + gid); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
    await a.fill('#ct-input', 'Hallo Gruppe'); await a.press('#ct-input', 'Enter');
    ok('Gruppe: ✓ als Knopf', !!(await until(async () => { const t = await lastTick(a); return t && t.text === '✓' ? t : null; }, 10000)));
    await b.goto(BASE + '/chats#c' + gid); await b.waitForSelector('#ct-chat:not([hidden])'); await b.waitForTimeout(800);
    const tg = await until(async () => { const t = await lastTick(a); return t && t.read ? t : null; }, 15000);
    ok('Gruppe: ✓✓ "Von allen gelesen"', tg && /allen/.test(tg.title), tg || await lastTick(a));
    await a.click('#ct-msgs .ct-msg.mine button.ct-tick'); await a.waitForSelector('.ct-readby');
    ok('Gruppe: "Gelesen von" listet B', (await a.textContent('.ct-readby')).indexOf(U2) >= 0, await a.textContent('.ct-readby'));
    await shot(a, 'gelesen-5-gruppe');
    await a.keyboard.press('Escape');
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])');
    await b.goto(BASE + '/chats#u' + encodeURIComponent(U1)); await b.waitForSelector('#ct-chat:not([hidden])'); await b.waitForTimeout(1200);
    // Datenschutz: B schaltet Gelesen-Bestaetigungen aus -> A bekommt kein ✓✓ mehr, B sieht keine von A (gegenseitig)
    ok('B: Schalter "Gelesen-Bestätigungen senden" aus', await privacy(b, 'receipts', false, 'gelesen-3-einstellungen'));
    await a.fill('#ct-input', 'Und das hier?'); await a.press('#ct-input', 'Enter');
    await b.waitForTimeout(3500);
    const t4 = await lastTick(a);
    ok('A: B hat abgeschaltet -> neue Nachricht bleibt ✓', t4 && t4.text === '✓' && !t4.read, t4);
    await b.fill('#ct-input', 'Ohne Haken'); await b.press('#ct-input', 'Enter');
    await a.waitForTimeout(3500);
    const t5 = await lastTick(b);
    ok('B (aus): sieht bei A auch kein ✓✓ (gegenseitig)', t5 && !t5.read, t5);
    ok('B: Schalter wieder an', await privacy(b, 'receipts', true));
    // Online-Status aus: A sieht nichts mehr von B
    ok('B: Schalter "Online-Status zeigen" aus', await privacy(b, 'online', false));
    ok('A: kein Online-Status von B mehr', await until(() => a.evaluate(() => !document.querySelector('#ct-sub .ct-online') && !document.querySelector('#ct-list .ct-av.ct-on')), 10000));
    ok('B (aus): sieht auch A nicht', await until(() => b.evaluate(() => !document.querySelector('#ct-sub .ct-online')), 10000));
    ok('B: Online-Status wieder an', await privacy(b, 'online', true));
    // Handy-Ansicht: Haken und Kopf passen
    const m = await session(browser, U1, E.SAGA_PASS, { viewport: { width: 390, height: 780 }, scheme: 'light' });
    await chatKeys(m, U1);
    await m.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await m.waitForSelector('#ct-chat:not([hidden])'); await m.waitForTimeout(2500);
    ok('Handy: Haken sichtbar, keine waagrechte Scrollleiste', await m.evaluate(() => !!document.querySelector('#ct-msgs .ct-tick') && document.documentElement.scrollWidth <= window.innerWidth + 1));
    await shot(m, 'gelesen-4-handy');
    ok('keine Seitenfehler', errs.length === 0, errs);
  } catch (e) {
    console.log(e); res.push(false);
  } finally {
    await browser.close();
  }
  console.log(`\nBROWSER-GELESEN ${res.filter(Boolean).length}/${res.length} - ` + (res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
