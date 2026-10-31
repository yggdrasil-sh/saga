// Browser-Ende-zu-Ende fuer die Gruppeninfo (2.5, static/chat.js, saga/chat.py PATCH /api/chats/{cid}/members/{who}) -
// drei Playwright-Kontexte (drei Konten) im headless Chrome:
//   SAGA_BASE=... SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... SAGA_USER3=c SAGA_PASS3=... [SAGA_GROUP=gruppe]
//   [SHOTS=/pfad] [SHOT_PREFIX=gi-] node tests/browser_gruppeninfo.js
// Die Konten muessen sich in der Personensuche finden (Einstellung "Suche nach Benutzernamen" an oder gemeinsame Gruppe);
// SAGA_GROUP = Saga-Gruppe mit A und B (fuer den Hinweis im Gruppen-Chat, sonst uebersprungen).
// Geprueft: Klick auf den Chat-Kopf oeffnet "Gruppeninfo" (auch per Tastatur), Menue "Gruppeninfo …", Name umbenennen im
// Blatt, Kennzeichen "Admin"/"Du", keine Anmeldenamen anderer im Blatt, B (kein Admin) sieht keine Verwaltung, Admin
// vergeben (B sieht danach live "Mitglied hinzufügen"), Admin entziehen, Mitglied entfernen mit Rueckfrage, letzter Admin
// (Hinweis vom Server), Gruppen-Chat nur lesend mit Hinweis; Handy-Breite als Blatt von unten (hell/dunkel); keine
// Seitenfehler. Kein waitForFunction (CSP ohne eval).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2 || !E.SAGA_USER3 || !E.SAGA_PASS3) {
  console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2, SAGA_USER3/PASS3 setzen'); process.exit(2);
}
const SHOTS = E.SHOTS || '', PRE = E.SHOT_PREFIX || 'gi-';
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
async function confirmAsk(p) { await p.waitForSelector('.hk-ask .btn-primary, .hk-ask .btn-destr'); await p.click('.hk-ask .btn-primary, .hk-ask .btn-destr'); }
// Zustand des offenen Blatts
const gi = p => p.evaluate(() => {
  const d = document.querySelector('.ct-gi'); if (!d) return null;
  return { title: (d.querySelector('.hk-dlg-head h2') || {}).textContent, name: (d.querySelector('.ct-gi-title') || {}).textContent || null,
           text: d.textContent, add: !!d.querySelector('.ct-gi-add'), edit: !!d.querySelector('.ct-gi-edit'), leave: !!d.querySelector('.ct-gi-leave'),
           ro: !!d.querySelector('.ct-gi-ro'),
           members: [...d.querySelectorAll('.ct-gi-m')].map(li => ({ n: (li.querySelector('.ct-gi-n') || {}).textContent, h: li.dataset.handle,
             admin: !!li.querySelector('.ct-gi-adm'), me: !!li.querySelector('.ct-gi-me'), more: !!li.querySelector('.ct-gi-more'),
             sub: (li.querySelector('.ct-gi-h2') || {}).textContent || '' })) };
});
const memberLi = (name) => '.ct-gi .ct-gi-m:has(.ct-gi-n:text-is("' + name + '"))';
async function memberAction(p, name, label) {
  await p.click(memberLi(name) + ' .ct-gi-more'); await p.waitForSelector('.menu .menu-item');
  const items = await p.$$eval('.menu .menu-item', x => x.map(i => i.textContent));
  if (label) await p.click('.menu .menu-item:has-text("' + label + '")');
  return items;
}
async function openInfoByHead(p) { await p.click('#ct-chat .ct-head-t'); return until(() => gi(p), 5000); }
async function closeInfo(p) { await p.keyboard.press('Escape'); await until(async () => !(await gi(p)), 3000); }

(async () => {
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2, U3 = E.SAGA_USER3;
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2), c = await session(browser, U3, E.SAGA_PASS3);
    await chatKeys(a, U1); await chatKeys(b, U2); await chatKeys(c, U3);
    ok('Chat-Schluessel in drei Browsern bereit', !(await a.$('.ct-ov')) && !(await b.$('.ct-ov')) && !(await c.$('.ct-ov')));
    const ver = await api(a, 'GET', '/api/version');
    ok('Server meldet chatadm1', (ver.d.features || []).includes('chatadm1'), ver.d.features);
    const pick = async (q) => ((await api(a, 'GET', '/api/chat/people?q=' + encodeURIComponent(q))).d.users || [])[0] || {};
    const pb = await pick(U2), pc = await pick(U3);
    const nB = pb.name, nC = pc.name;
    const mk = await api(a, 'POST', '/api/chats', { kind: 'free', members: [pb.username, pc.username] });
    ok('freie Gruppe A, B, C angelegt', mk.s === 200 && mk.d.chat.me_admin === true && mk.d.chat.can_manage === true, mk);
    const gid = mk.d.chat.id;
    await a.goto(BASE + '/chats#c' + gid); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);

    // --- Oeffnen ueber den Kopf, Inhalt -------------------------------------------------------------------------------
    let g = await openInfoByHead(a);
    ok('Klick auf den Chat-Kopf oeffnet "Gruppeninfo"', g && g.title === 'Gruppeninfo', g && g.title);
    ok('Blatt: drei Mitglieder, "Du" und "Admin" bei mir', g && g.members.length === 3 && g.members[0].me && g.members[0].admin
       && g.members.filter(m => m.admin).length === 1, g && g.members);
    ok('Blatt: Mitglied hinzufuegen, Stift, Gruppe verlassen; Menue je Mitglied', g && g.add && g.edit && g.leave && g.members.filter(m => !m.me).every(m => m.more));
    ok('Blatt: keine Anmeldenamen anderer (nur Name und Handle)', g && g.text.indexOf(U2) < 0 && g.text.indexOf(U3) < 0
       && g.members.filter(m => !m.me).every(m => /^~/.test(m.h)), g && g.members);
    await shot(a, 'desktop-hell');
    // Umbenennen im Blatt
    await a.click('.ct-gi-edit'); await a.fill('.ct-gi-in', 'Wandergruppe'); await a.click('.ct-gi-save');
    g = await until(async () => { const x = await gi(a); return x && x.name === 'Wandergruppe' ? x : null; }, 10000);
    ok('Name im Blatt umbenannt (verschluesselte title-Nachricht), Kopf aktualisiert', !!g && (await a.textContent('#ct-title')).indexOf('Wandergruppe') >= 0);
    await closeInfo(a);
    // Tastatur + Menue
    await a.focus('#ct-chat .ct-head-t'); await a.keyboard.press('Enter');
    ok('Kopf per Tastatur (Enter) oeffnet die Gruppeninfo', !!(await until(() => gi(a), 3000)));
    await closeInfo(a);
    await a.click('#ct-more'); await a.waitForSelector('.menu .menu-item');
    const items = await a.$$eval('.menu .menu-item', x => x.map(i => i.textContent));
    ok('Menue ⋯ hat "Gruppeninfo …" (statt "Mitglieder")', items.some(t => /Gruppeninfo …/.test(t)) && !items.some(t => /^👥\s*Mitglieder/.test(t)), items);
    await a.click('.menu .menu-item:has-text("Gruppeninfo")');
    ok('… und oeffnet sie', !!(await until(() => gi(a), 3000)));

    // --- B ohne Rechte -------------------------------------------------------------------------------------------------
    await b.goto(BASE + '/chats#c' + gid); await b.waitForSelector('#ct-chat:not([hidden])'); await b.waitForTimeout(1200);
    let gb = await openInfoByHead(b);
    ok('B (kein Admin): Blatt ohne Hinzufuegen, Umbenennen und Verlassen ja', gb && !gb.add && gb.edit && gb.leave, gb);
    const bItems = await memberAction(b, nC, null); await b.keyboard.press('Escape');
    ok('B: Menue je Mitglied nur "Sicherheitsnummer"', bItems.length === 1 && /Sicherheitsnummer/.test(bItems[0]), bItems);

    // --- Admin vergeben ------------------------------------------------------------------------------------------------
    const aItems = await memberAction(a, nB, 'Zum Admin machen');
    ok('A: Menue je Mitglied "Zum Admin machen" und "Aus Gruppe entfernen"', aItems.some(t => /Zum Admin machen/.test(t)) && aItems.some(t => /Aus Gruppe entfernen/.test(t)), aItems);
    g = await until(async () => { const x = await gi(a); return x && x.members.find(m => m.n === nB && m.admin) ? x : null; }, 10000);
    ok('A: B ist im Blatt jetzt "Admin"', !!g, await gi(a));
    gb = await until(async () => { const x = await gi(b); return x && x.add ? x : null; }, 15000);
    ok('B: Blatt aktualisiert sich live - "Mitglied hinzufügen" sichtbar, eigenes "Admin"', !!gb && gb.members.find(m => m.me).admin, await gi(b));
    // Admin entziehen
    await memberAction(a, nB, 'Admin entziehen');
    g = await until(async () => { const x = await gi(a); return x && !x.members.find(m => m.n === nB).admin ? x : null; }, 10000);
    ok('A: Admin entzogen', !!g);
    // Letzter Admin: sich selbst entziehen -> Hinweis vom Server
    const self = await memberAction(a, (await gi(a)).members.find(m => m.me).n, null);
    ok('A (einziger Admin): eigenes Menue bietet "Admin entziehen"', self.some(t => /Admin entziehen/.test(t)), self);
    await a.click('.menu .menu-item:has-text("Admin entziehen")');
    const toast = await until(() => a.evaluate(() => { const t = [...document.querySelectorAll('.hk-toast, .toast, [role=status]')].map(x => x.textContent).join(' '); return /letzte Admin/.test(t) ? t : null; }), 5000);
    ok('Letzter Admin: verstaendliche Meldung, bleibt Admin', !!toast && (await gi(a)).members.find(m => m.me).admin, toast);

    // --- Mitglied entfernen --------------------------------------------------------------------------------------------
    await memberAction(a, nC, 'Aus Gruppe entfernen');
    const askT = await a.textContent('.hk-ask');
    ok('Entfernen fragt nach', /entfernen\?/.test(askT), askT);
    await confirmAsk(a);
    g = await until(async () => { const x = await gi(a); return x && x.members.length === 2 ? x : null; }, 10000);
    ok('C entfernt: Blatt mit zwei Mitgliedern', !!g && !g.members.some(m => m.n === nC), await gi(a));
    const cview = await until(async () => (await api(c, 'GET', '/api/chats/' + gid)).s === 404, 10000);
    ok('C gehoert nicht mehr dazu', cview);
    // Hinzufuegen ueber die vorhandene Suche
    await a.click('.ct-gi-add'); await a.fill('.ct-gi-search input', U3);
    await a.waitForSelector('.ct-gi-search .ct-person', { timeout: 8000 });
    await a.click('.ct-gi-search .ct-person');
    g = await until(async () => { const x = await gi(a); return x && x.members.length === 3 ? x : null; }, 10000);
    ok('"Mitglied hinzufügen": C wieder dabei, Blatt bleibt offen', !!g);
    await closeInfo(a); await closeInfo(b);

    // --- Gruppen-Chat: nur lesend ----------------------------------------------------------------------------------------
    if (E.SAGA_GROUP) {
      const gr = await api(a, 'POST', '/api/chats', { kind: 'group', group: E.SAGA_GROUP });
      await a.goto(BASE + '/chats#c' + gr.d.chat.id); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
      const gg = await openInfoByHead(a);
      ok('Gruppen-Chat: Blatt nur lesend mit Hinweis', gg && gg.ro && !gg.add && !gg.edit && !gg.leave && /Freigaben der Seite/.test(gg.text)
         && gg.members.every(m => !m.admin), gg);
      await shot(a, 'gruppe-hell');
      await closeInfo(a);
    } else console.log('(SAGA_GROUP nicht gesetzt - Gruppen-Chat uebersprungen)');

    // --- Dunkel, Handy-Breite ------------------------------------------------------------------------------------------
    await a.goto(BASE + '/chats#c' + gid); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1000);
    await a.emulateMedia({ colorScheme: 'dark' });
    await openInfoByHead(a); await shot(a, 'desktop-dunkel'); await closeInfo(a);
    const h = await session(browser, U1, E.SAGA_PASS, { viewport: { width: 390, height: 844 }, mobile: true, scheme: 'light' });
    await chatKeys(h, U1);
    await h.goto(BASE + '/chats#c' + gid); await h.waitForSelector('#ct-chat:not([hidden])'); await h.waitForTimeout(1200);
    await h.tap('#ct-chat .ct-head-t');
    const gh = await until(() => gi(h), 5000);
    const box = await h.evaluate(() => { const r = document.querySelector('.ct-gi').getBoundingClientRect(); return { l: r.left, r: r.right, b: r.bottom, t: r.top, w: innerWidth, h: innerHeight,
      sw: document.documentElement.scrollWidth }; });
    ok('Handy: Tippen auf den Kopf oeffnet die Gruppeninfo als Blatt von unten (volle Breite, ohne Querscrollen)', !!gh
       && Math.abs(box.b - box.h) < 2 && box.l <= 1 && box.r >= box.w - 1 && box.sw <= box.w, box);
    await shot(h, 'handy-hell');
    await h.tap(memberLi(nB) + ' .ct-gi-more'); await h.waitForSelector('.menu .menu-item'); await h.waitForTimeout(200);
    await shot(h, 'handy-hell-menue');
    await h.keyboard.press('Escape');
    await h.emulateMedia({ colorScheme: 'dark' }); await h.waitForTimeout(200);
    await shot(h, 'handy-dunkel');
    ok('Keine Seitenfehler', errs.length === 0, errs);
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e).slice(0, 800));
  } finally {
    await browser.close();
  }
  const n = res.filter(Boolean).length;
  console.log(`\nBROWSER-GRUPPENINFO ${n}/${res.length} OK – ` + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
