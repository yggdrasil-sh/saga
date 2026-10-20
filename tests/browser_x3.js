// Browser-Ende-zu-Ende fuer Reaktionen und Bearbeiten (2.5, static/chat.js + static/chat-emoji.js, saga/chat_x3.py) - zwei
// Playwright-Kontexte (zwei Konten) im headless Chrome gegen einen Test-Stack mit Chats der Stufe 3 (tests/browser_v3.py
// schaltet chat_v3 ein und raeumt die Chats der beiden vorher weg):
//   SAGA_BASE=... SAGA_USER=konto-a SAGA_PASS=... SAGA_USER2=konto-b SAGA_PASS2=... [SHOTS=/pfad] node tests/browser_x3.js
// Geprueft: Schnellleiste (6 Emojis + "+"), lokale Emoji-Auswahl, Chips mit Anzahl (eigene hervorgehoben), Ersetzen und
// Zuruecknehmen, live beim anderen; Bearbeiten ueber Pfeil hoch und ueber das Menue, Leiste "Bearbeiten · Abbrechen",
// Hinweis "bearbeitet" (auch live beim anderen), Esc bricht ab; hell/dunkel, Handy-Breite; keine Seitenfehler.
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
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: opts.scheme || 'dark',
                                         isMobile: !!opts.mobile, hasTouch: !!opts.mobile });
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
// Nachricht mit Text t: Reaktions-Chips {e, n, mine}, Text, "bearbeitet"
const msgInfo = (p, t) => p.evaluate(t => {
  const row = [...document.querySelectorAll('#ct-msgs .ct-msg')].filter(r => (r.querySelector('.ct-text') || {}).textContent === t).pop();
  if (!row) return null;
  return { id: row.dataset.id, mine: row.classList.contains('mine'), edited: !!row.querySelector('.ct-edited'),
           chips: [...row.querySelectorAll('.ct-rx-chip')].map(c => ({ e: c.querySelector('.ct-rx-e').textContent, n: +c.querySelector('.ct-rx-n').textContent, mine: c.classList.contains('mine') })),
           reactBtn: !!row.querySelector('.ct-react-b') };
}, t);
const rowSel = id => '#ct-msgs .ct-msg[data-id="' + id + '"]';
async function openChat(p) { await p.goto(BASE + '/chats'); await p.waitForTimeout(1200); await p.click('#ct-list .ct-item-b'); await p.waitForSelector('#ct-chat:not([hidden])'); await p.waitForTimeout(1000); }

(async () => {
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2);
    await chatKeys(a, U1); await chatKeys(b, U2);
    ok('Chat-Schluessel in beiden Browsern bereit', !(await a.$('.ct-ov')) && !(await b.$('.ct-ov')));
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
    await a.fill('#ct-input', 'Hallo, magst du Reaktionen?'); await a.press('#ct-input', 'Enter');
    await openChat(b);
    const m1 = await until(() => msgInfo(b, 'Hallo, magst du Reaktionen?'), 15000);
    ok('B: Nachricht von A angekommen, Knopf "Reagieren" da (x3_ok)', m1 && m1.reactBtn, m1);
    const cur = await b.evaluate(() => fetch('/api/chats?device=', { headers: { 'X-Saga': '1', 'X-Saga-Chat': '3' } }).then(r => r.json()).then(d => d.chats.map(c => ({ v: c.v, x3: c.x3_ok }))));
    ok('Chat in Stufe 3 mit x3_ok', cur.length && cur.every(c => c.v === 3 && c.x3 === true), cur);
    // Schnellleiste
    await b.hover(rowSel(m1.id)); await b.click(rowSel(m1.id) + ' .ct-react-b');
    await b.waitForSelector('.menu .ct-rq');
    const quick = await b.$$eval('.menu .ct-rq .ct-rq-b', bs => bs.map(x => x.textContent));
    ok('Schnellleiste: 6 Emojis und "+"', quick.length === 7 && quick[0] === '👍' && quick[6] === '+', quick);
    await shot(b, 'x3-1-schnellleiste-dunkel');
    await b.click('.menu .ct-rq .ct-rq-b >> nth=0');
    const c1 = await until(async () => { const x = await msgInfo(b, 'Hallo, magst du Reaktionen?'); return x && x.chips.length === 1 ? x : null; }, 5000);
    ok('B: Chip 👍 1, eigene hervorgehoben', c1 && c1.chips[0].e === '👍' && c1.chips[0].n === 1 && c1.chips[0].mine, c1);
    const a1 = await until(async () => { const x = await msgInfo(a, 'Hallo, magst du Reaktionen?'); return x && x.chips.length === 1 ? x : null; }, 15000);
    ok('A: Reaktion live angekommen (geprueft, geoeffnet), nicht als eigene', a1 && a1.chips[0].e === '👍' && !a1.chips[0].mine, a1 || await msgInfo(a, 'Hallo, magst du Reaktionen?'));
    // Emoji-Auswahl ("+"), ersetzt die eigene Reaktion
    await b.hover(rowSel(m1.id)); await b.click(rowSel(m1.id) + ' .ct-react-b'); await b.waitForSelector('.menu .ct-rq-more');
    await b.click('.menu .ct-rq-more'); await b.waitForSelector('.menu .em-grid .em-b');
    const tabs = await b.$$eval('.menu .em-tab', t => t.length);
    const rec = await b.$$eval('.menu .em-grid .em-b', x => x.map(i => i.textContent));
    ok('Emoji-Auswahl: Reiter (Zuletzt + 9 Gruppen), zuerst "Zuletzt benutzt" mit 👍', tabs === 10 && rec[0] === '👍', { tabs, rec });
    await b.click('.menu .em-tab >> nth=1');
    ok('Emoji-Auswahl: Raster der Smileys (lokal, ohne CDN)', (await b.$$('.menu .em-grid .em-b')).length > 40);
    await shot(b, 'x3-2-emoji-auswahl-dunkel');
    await b.click('.menu .em-tab >> nth=3');                  // Herzen (Zuletzt + Smileys + Gesten davor)
    await b.click('.menu .em-grid .em-b >> nth=4');           // 💙
    const c2 = await until(async () => { const x = await msgInfo(a, 'Hallo, magst du Reaktionen?'); return x && x.chips.length === 1 && x.chips[0].e !== '👍' ? x : null; }, 15000);
    ok('A: B hat ersetzt (eine je Person)', c2 && c2.chips[0].e === '💙' && c2.chips[0].n === 1, c2 || await msgInfo(a, 'Hallo, magst du Reaktionen?'));
    // A tippt auf den Chip -> gleiche Reaktion (2), nochmal -> zurueck (1)
    await a.click(rowSel(m1.id) + ' .ct-rx-chip');
    const c3 = await until(async () => { const x = await msgInfo(b, 'Hallo, magst du Reaktionen?'); return x && x.chips.length === 1 && x.chips[0].n === 2 ? x : null; }, 15000);
    ok('B: A reagiert ueber den Chip mit -> 💙 2', !!c3, await msgInfo(b, 'Hallo, magst du Reaktionen?'));
    await shot(a, 'x3-3-chips-dunkel');
    await a.waitForTimeout(800);
    await a.click(rowSel(m1.id) + ' .ct-rx-chip');
    const c4 = await until(async () => { const x = await msgInfo(b, 'Hallo, magst du Reaktionen?'); return x && x.chips.length === 1 && x.chips[0].n === 1 ? x : null; }, 15000);
    ok('B: A nimmt zurueck -> 💙 1', !!c4, await msgInfo(b, 'Hallo, magst du Reaktionen?'));
    // Schnell hintereinander: nur die letzte Wahl geht raus (gebuendelt)
    let puts = 0; a.on('request', r => { if (r.method() === 'PUT' && /\/reaction$/.test(r.url())) puts++; });
    for (const i of [0, 1, 2]) { await a.hover(rowSel(m1.id)); await a.click(rowSel(m1.id) + ' .ct-react-b'); await a.waitForSelector('.menu .ct-rq'); await a.click('.menu .ct-rq .ct-rq-b >> nth=' + i); }
    const c5 = await until(async () => { const x = await msgInfo(b, 'Hallo, magst du Reaktionen?'); return x && x.chips.some(c => c.e === '😂') ? x : null; }, 15000);
    ok('Schnell gewechselt: B sieht nur die letzte Wahl (😂), ein Aufruf', !!c5 && c5.chips.length === 2 && puts === 1, { c5, puts });
    // Bearbeiten: Pfeil hoch im leeren Feld
    await a.fill('#ct-input', 'Das ist ein Tippfehlr'); await a.press('#ct-input', 'Enter');
    await until(() => msgInfo(a, 'Das ist ein Tippfehlr'), 10000); await a.waitForTimeout(600);
    await a.focus('#ct-input'); await a.press('#ct-input', 'ArrowUp');
    const bar = await until(() => a.evaluate(() => { const r = document.querySelector('#ct-reply.ct-editing'); return r && !r.hidden ? { t: r.textContent, v: document.querySelector('#ct-input').value } : null; }), 3000);
    ok('A: Pfeil hoch -> Leiste "Bearbeiten · Abbrechen", Text im Feld', bar && /Bearbeiten/.test(bar.t) && /Abbrechen/.test(bar.t) && bar.v === 'Das ist ein Tippfehlr', bar);
    await shot(a, 'x3-4-bearbeiten-leiste-dunkel');
    await a.fill('#ct-input', 'Das ist ein Tippfehler'); await a.press('#ct-input', 'Enter');
    const e1 = await until(async () => { const x = await msgInfo(a, 'Das ist ein Tippfehler'); return x && x.edited ? x : null; }, 10000);
    ok('A: neuer Text mit "bearbeitet"', !!e1, e1);
    const e2 = await until(async () => { const x = await msgInfo(b, 'Das ist ein Tippfehler'); return x && x.edited ? x : null; }, 15000);
    ok('B: Bearbeitung live angekommen (neuester Text, "bearbeitet")', !!e2 && !(await msgInfo(b, 'Das ist ein Tippfehlr')), e2);
    // Bearbeiten ueber das Menue, Esc bricht ab
    await a.hover(rowSel(e1.id)); await a.click(rowSel(e1.id) + ' .ct-act:not(.ct-react-b)'); await a.waitForSelector('.menu .menu-item');
    const items = await a.$$eval('.menu .menu-item', x => x.map(i => i.textContent));
    ok('A: Menue mit "Bearbeiten" (und Reaktionen oben)', items.some(t => /Bearbeiten/.test(t)) && !!(await a.$('.menu .ct-rq')), items);
    await shot(a, 'x3-5-menue-dunkel');
    await a.click('.menu .menu-item:has-text("Bearbeiten")');
    await a.waitForSelector('#ct-reply.ct-editing:not([hidden])');
    await a.press('#ct-input', 'Escape');
    ok('A: Esc bricht ab (Leiste weg, Feld leer)', await until(() => a.evaluate(() => document.querySelector('#ct-reply').hidden && !document.querySelector('#ct-input').value), 3000));
    const bm = await msgInfo(b, 'Hallo, magst du Reaktionen?');
    await b.hover(rowSel(bm.id)); await b.click(rowSel(bm.id) + ' .ct-act:not(.ct-react-b)'); await b.waitForSelector('.menu .menu-item');
    ok('B: fremde Nachricht ohne "Bearbeiten"', !(await b.$$eval('.menu .menu-item', x => x.map(i => i.textContent))).some(t => /Bearbeiten/.test(t)));
    await b.keyboard.press('Escape');
    // Erste Nachricht des Chats bearbeiten: Gegenseite sieht es live (chat.edited -> Nachricht einzeln nachladen)
    const f0 = await msgInfo(a, 'Hallo, magst du Reaktionen?');
    await a.hover(rowSel(f0.id)); await a.click(rowSel(f0.id) + ' .ct-act:not(.ct-react-b)'); await a.waitForSelector('.menu .menu-item');
    await a.click('.menu .menu-item:has-text("Bearbeiten")'); await a.waitForSelector('#ct-reply.ct-editing:not([hidden])');
    await a.fill('#ct-input', 'Hallo, magst du Reaktionen? Bitte!'); await a.press('#ct-input', 'Enter');
    const fe = await until(async () => { const x = await msgInfo(b, 'Hallo, magst du Reaktionen? Bitte!'); return x && x.edited ? x : null; }, 15000);
    ok('B: Bearbeitung der ersten Nachricht live angekommen (Reaktionen bleiben)', !!fe && fe.id === f0.id && fe.chips.length === 2, fe || await msgInfo(b, 'Hallo, magst du Reaktionen?'));
    // Hell + Handy-Breite
    const c = b;                                            // dasselbe Konto/Geraet, jetzt hell und schmal
    await c.setViewportSize({ width: 390, height: 844 }); await c.emulateMedia({ colorScheme: 'light' }); await openChat(c);
    await until(() => msgInfo(c, 'Das ist ein Tippfehler'), 10000);
    const ov = await c.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
    ok('Handy (hell): keine waagrechte Scrollleiste', ov);
    await shot(c, 'x3-6-handy-hell');
    const cm = await msgInfo(c, 'Hallo, magst du Reaktionen? Bitte!');
    await c.click(rowSel(cm.id) + ' .ct-react-b'); await c.waitForSelector('.menu .ct-rq');
    const box = await c.$eval('.menu', m => { const r = m.getBoundingClientRect(); return { l: r.left, r: r.right, w: window.innerWidth }; });
    ok('Handy: Schnellleiste passt in die Breite', box.l >= 0 && box.r <= box.w + 1, box);
    await shot(c, 'x3-7-handy-schnellleiste-hell');
    await c.click('.menu .ct-rq-more'); await c.waitForSelector('.menu .em-grid .em-b');
    const box2 = await c.$eval('.menu', m => { const r = m.getBoundingClientRect(); return { l: r.left, r: r.right, w: window.innerWidth }; });
    ok('Handy: Emoji-Auswahl passt in die Breite', box2.l >= 0 && box2.r <= box2.w + 1, box2);
    await shot(c, 'x3-8-handy-emoji-hell');
    await c.keyboard.press('Escape');
    const d = a;
    await d.emulateMedia({ colorScheme: 'light' }); await openChat(d);
    await until(() => msgInfo(d, 'Das ist ein Tippfehler'), 10000);
    await shot(d, 'x3-9-desktop-hell');
    ok('keine Seitenfehler', errs.length === 0, errs);
  } catch (e) { console.log('FAIL Ausnahme: ' + (e && e.stack || e)); res.push(false); }
  await browser.close();
  const n = res.filter(Boolean).length;
  console.log('\nBROWSER-X3 ' + n + '/' + res.length + ' - ' + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
