// Browser-Ende-zu-Ende fuer die Kontakte (2.5, contacts1, static/chat.js) - zwei Playwright-Kontexte im headless Chrome:
//   SAGA_BASE=... SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... SAGA_USER3=c SAGA_PASS3=... [SHOTS=/pfad] [SHOT_PREFIX=ko-]
//   node tests/browser_kontakte.js
// Die Konten A, B, C brauchen Anzeigenamen; A und B duerfen noch keinen gemeinsamen Chat haben (Kontakt entsteht im Test).
// Geprueft: "Neue Unterhaltung" ohne Eingabe -> Liste "Kontakte" (zuletzt zuerst, scrollbar), Kontakt bleibt nach dem
// Loeschen des Chats, mit Eingabe (2 Zeichen) Kontakte zuerst, Klick beginnt den Direkt-Chat, Menue "Aus Kontakten entfernen"
// (weg aus der Liste, Server ausgeblendet); "Mitglied hinzufügen" einer freien Gruppe (Gruppeninfo) zeigt Kontakte, die noch
// nicht Mitglied sind; keine Anmeldenamen anderer in den Dialogen; Handy-Breite, hell/dunkel (Screenshots); keine
// Seitenfehler. Kein waitForFunction (CSP ohne eval).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2 || !E.SAGA_USER3 || !E.SAGA_PASS3) {
  console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2, SAGA_USER3/PASS3 setzen'); process.exit(2);
}
const SHOTS = E.SHOTS || '', PRE = E.SHOT_PREFIX || 'ko-';
const res = [], errs = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, PRE + name + '.png') }); } }
async function until(fn, ms = 15000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

async function session(browser, user, pass, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: opts.scheme || 'light',
                                         isMobile: !!opts.mobile, hasTouch: !!opts.mobile, deviceScaleFactor: opts.mobile ? 2 : 1 });
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
const api = (p, m, u, b) => p.evaluate(([m, u, b]) => fetch(u, { method: m, headers: { 'X-Saga': '1', 'X-Saga-Chat': '3', 'Content-Type': 'application/json' },
  body: b ? JSON.stringify(b) : undefined }).then(r => r.json().then(d => ({ s: r.status, d }))), [m, u, b]);
// Zustand der Personenliste im offenen Dialog
const list = p => p.evaluate(() => {
  const ul = document.querySelector('.ct-ov .ct-people-list'); if (!ul) return null;
  const cs = getComputedStyle(ul);
  return { head: [...ul.querySelectorAll('.ct-people-h')].map(x => x.textContent), text: document.querySelector('.ct-ov').textContent,
           scroll: cs.overflowY, rows: [...ul.querySelectorAll('li')].filter(li => li.querySelector('.ct-person')).map(li => ({
             n: (li.querySelector('.ct-item-n') || {}).textContent, more: !!li.querySelector('.ct-contact-more') })) };
});
async function openNew(p) {
  await p.click('#ct-new'); await p.waitForSelector('.ct-ov .ct-people-q');
  await until(async () => ((await list(p)) || {}).head && (await list(p)).head.length, 6000);
}

(async () => {
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || undefined, args: ['--no-sandbox'] });
  try {
    const A = await session(browser, E.SAGA_USER, E.SAGA_PASS), B = await session(browser, E.SAGA_USER2, E.SAGA_PASS2);
    const Cp = await session(browser, E.SAGA_USER3, E.SAGA_PASS3);
    await chatKeys(A, E.SAGA_USER); await chatKeys(B, E.SAGA_USER2); await chatKeys(Cp, E.SAGA_USER3);
    const nameOf = async q => (await api(q, 'GET', '/api/chat/me')).d.name;
    const nA = await nameOf(A), nB = await nameOf(B), nC = await nameOf(Cp);
    const ref = async (q, n) => (((await api(q, 'GET', '/api/chat/people?q=' + encodeURIComponent(n))).d.users || []).find(u => u.name === n) || {}).username;
    const rB = await ref(A, nB), rC = await ref(A, nC);
    ok('Personensuche liefert B und C als Handle', /^~/.test(rB || '') && /^~/.test(rC || ''), [rB, rC]);
    // Kontakte entstehen: Direkt-Chat A-B, dann fuer alle loeschen (Kontakt bleibt); C kommt spaeter
    let r = await api(A, 'POST', '/api/chats', { kind: 'dm', user: rB });
    ok('Direkt-Chat A-B angelegt (' + r.s + ')', r.s === 200);
    r = await api(A, 'DELETE', '/api/chats/' + r.d.chat.id + '?scope=all');
    ok('Direkt-Chat fuer alle geloescht (' + r.s + ')', r.s === 200);
    await A.goto(BASE + '/chats'); await A.waitForTimeout(1500);
    await openNew(A);
    let l = await list(A);
    ok('"Neue Unterhaltung" ohne Eingabe: Liste Kontakte mit B (nach Loeschen des Chats)', l && l.head[0] === 'Kontakte' && l.rows.some(x => x.n === nB && x.more), l);
    ok('Liste scrollbar', l && /auto|scroll/.test(l.scroll), l && l.scroll);
    ok('Keine Anmeldenamen anderer im Dialog', l && !l.text.includes(E.SAGA_USER2) && !l.text.includes(E.SAGA_USER3));
    await shot(A, 'neu-desktop-hell');
    // Eingabe 2 Zeichen: Kontakt zuerst
    await A.fill('.ct-ov .ct-people-q', nB.slice(0, 2));
    await until(async () => { const x = await list(A); return x && !x.head.length && x.rows.length; }, 6000);
    l = await list(A);
    ok('Mit Eingabe (2 Zeichen): Kontakt B oben', l && l.rows[0] && l.rows[0].n === nB, l);
    // Klick beginnt den Direkt-Chat
    await A.click('.ct-ov .ct-people-list .ct-person');
    ok('Klick auf den Kontakt oeffnet den Direkt-Chat', await until(async () => !(await A.$('.ct-ov')) && /#c\d+/.test(A.url()), 8000), A.url());
    // C: freie Gruppe A + C, dann Gruppe mit A, B neu -> "Mitglied hinzufuegen" zeigt C als Kontakt
    r = await api(A, 'POST', '/api/chats', { kind: 'free', members: [rC] });
    ok('Freie Gruppe A, C (' + r.s + ')', r.s === 200);
    r = await api(A, 'POST', '/api/chats', { kind: 'free', members: [rB] });
    const gid = r.d.chat.id;
    await A.goto(BASE + '/chats#c' + gid); await A.waitForTimeout(2500);
    await A.click('#ct-chat .ct-head-t').catch(() => {});
    const giOpen = await until(async () => !!(await A.$('.ct-gi')), 4000);
    if (giOpen) {
      await A.click('.ct-gi-add');
      await until(async () => { const x = await list(A); return x && x.head.length; }, 6000);
      l = await list(A);
      ok('Gruppeninfo "Mitglied hinzufügen": Kontakte ohne Mitglieder (C ja, B nicht)', l && l.head[0] === 'Kontakte' && l.rows.some(x => x.n === nC)
         && !l.rows.some(x => x.n === nB), l);
      await shot(A, 'hinzufuegen-desktop-hell');
      await A.click('.ct-ov .ct-people-list .ct-person');
      ok('Klick fuegt C hinzu', await until(async () => ((await api(A, 'GET', '/api/chats/' + gid)).d.chat.members || []).length === 3, 8000));
    } else ok('Gruppeninfo-Dialog geoeffnet', false);
    await A.keyboard.press('Escape'); await A.waitForTimeout(300);
    // Aus Kontakten entfernen
    await A.goto(BASE + '/chats'); await A.waitForTimeout(1500);
    await openNew(A);
    const rows = await A.$$('.ct-ov .ct-people-list li.ct-contact');
    let target = null;
    for (const li of rows) if (((await li.textContent()) || '').includes(nC)) target = li;
    await (await target.$('.ct-contact-more')).click();
    await A.waitForSelector('.menu-item.danger');
    await shot(A, 'menue-desktop-hell');
    await A.click('.menu-item.danger');
    ok('"Aus Kontakten entfernen": aus der Liste weg', await until(async () => !((await list(A)) || { rows: [] }).rows.some(x => x.n === nC), 5000));
    const kc = (await api(A, 'GET', '/api/chat/contacts')).d.contacts || [];
    ok('Server: C nicht mehr in den Kontakten, B schon', !kc.some(k => k.name === nC) && kc.some(k => k.name === nB), kc.map(k => k.name));
    await A.keyboard.press('Escape');
    // Handy-Breite, hell/dunkel; Desktop dunkel
    for (const [scheme, mobile] of [['dark', false], ['light', true], ['dark', true]]) {
      const q = await session(browser, E.SAGA_USER, E.SAGA_PASS, { scheme, mobile, viewport: mobile ? { width: 390, height: 780 } : { width: 1280, height: 800 } });
      await chatKeys(q, E.SAGA_USER);
      await q.goto(BASE + '/chats'); await q.waitForTimeout(1500);
      await openNew(q);
      l = await list(q);
      const w = await q.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
      ok(`${mobile ? 'Handy' : 'Desktop'} ${scheme}: Kontakte sichtbar, keine waagerechte Rolle`, l && l.rows.some(x => x.n === nB) && w[0] <= w[1], [l && l.rows, w]);
      await shot(q, `neu-${mobile ? 'handy' : 'desktop'}-${scheme === 'dark' ? 'dunkel' : 'hell'}`);
      await q.context().close();
    }
    ok('Keine Seitenfehler', !errs.length, errs);
  } catch (e) {
    console.log(e.stack || e); res.push(false);
  } finally {
    await browser.close();
  }
  console.log(`\nBROWSER-KONTAKTE ${res.filter(Boolean).length}/${res.length} - ` + (res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
