// Browser-Ende-zu-Ende fuer Chat-Standard B (2.5, static/chat.js, saga/chat_grp.py) - drei Playwright-Kontexte (drei Konten):
//   SAGA_BASE=... SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... SAGA_USER3=c SAGA_PASS3=... [SHOTS=/pfad] [SHOT_PREFIX=g2-]
//   node tests/browser_gruppen2.js
// A legt eine freie Gruppe mit B und C an (A Admin). Geprueft: Beschreibung in der Gruppeninfo setzen (verschluesselt, B sieht
// sie live), Gruppenbild hochladen (quadratisch zugeschnitten, B sieht es), Rechte-Schalter nur fuer Admins ("Nur Admins
// duerfen Nachrichten senden" -> B: Eingabefeld weg mit Hinweis), Nachricht anheften (Leiste oben bei allen, Loesen),
// Admin-Loeschen einer fremden Nachricht ("Von einem Admin geloescht" live bei B), Sperren einer Person aus dem
// Nachrichtenmenue (Nachrichten eingeklappt mit "Anzeigen"), Melden mit Grund und mitgeschicktem Text; Handy-Breite
// hell/dunkel; keine Seitenfehler. Kein waitForFunction (CSP ohne eval).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2 || !E.SAGA_USER3 || !E.SAGA_PASS3) {
  console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2, SAGA_USER3/PASS3 setzen'); process.exit(2);
}
const SHOTS = E.SHOTS || '', PRE = E.SHOT_PREFIX || 'g2-';
const res = [], errs = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 400))); }
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
async function openC(p, id) { await p.goto(BASE + '/chats#c' + id); await p.reload(); await p.waitForSelector('#ct-chat:not([hidden])'); await p.waitForTimeout(1500); }
async function openInfo(p) { await p.click('#ct-chat .ct-head-t'); await p.waitForSelector('.ct-gi'); await p.waitForTimeout(300); }
async function closeInfo(p) { await p.keyboard.press('Escape'); await until(async () => !(await p.$('.ct-gi')), 3000); }
async function send(p, text) {
  await p.fill('#ct-input', text); await p.press('#ct-input', 'Enter');
  return until(() => p.$('.ct-msg.mine .ct-text:text-is("' + text + '")'), 15000);
}
const rowSel = t => '.ct-msg:has(.ct-text:text-is("' + t + '"))';
async function msgMenu(p, text, label) {
  await p.hover(rowSel(text)); await p.click(rowSel(text) + ' .ct-act:not(.ct-react-b)'); await p.waitForSelector('.menu .menu-item');
  const items = await p.$$eval('.menu .menu-item', x => x.map(i => i.textContent));
  if (label) await p.click('.menu .menu-item:has-text("' + label + '")'); else await p.keyboard.press('Escape');
  return items;
}
// kleines gueltiges PNG (rechteckig 6 x 3, RGB) fuer das Gruppenbild
const zlib = require('zlib');
function png() {
  const crc = buf => { let c, t = []; for (let n = 0; n < 256; n++) { c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    let x = 0xffffffff; for (const v of buf) x = t[(x ^ v) & 0xff] ^ (x >>> 8); return (x ^ 0xffffffff) >>> 0; };
  const chunk = (type, data) => { const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(type), data]);
    const cr = Buffer.alloc(4); cr.writeUInt32BE(crc(td)); return Buffer.concat([len, td, cr]); };
  const w = 6, h = 3, ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  const raw = Buffer.alloc((w * 3 + 1) * h); for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const o = y * (w * 3 + 1) + 1 + x * 3; raw[o] = 40 * x; raw[o + 1] = 120; raw[o + 2] = 200; }
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

(async () => {
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2, U3 = E.SAGA_USER3;
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2), c = await session(browser, U3, E.SAGA_PASS3);
    await chatKeys(a, U1); await chatKeys(b, U2); await chatKeys(c, U3);
    ok('Chat-Schluessel in drei Browsern bereit', !(await a.$('.ct-ov')) && !(await b.$('.ct-ov')) && !(await c.$('.ct-ov')));
    const ver = await api(a, 'GET', '/api/version');
    ok('Server meldet groupinfo2, moddel1, msgpin1, block2, report1', ['groupinfo2', 'moddel1', 'msgpin1', 'block2', 'report1'].every(f => (ver.d.features || []).includes(f)));
    const pick = async (q) => ((await api(a, 'GET', '/api/chat/people?q=' + encodeURIComponent(q))).d.users || [])[0] || {};
    const pb = await pick(U2), pc = await pick(U3), nC = pc.name;
    const mk = await api(a, 'POST', '/api/chats', { kind: 'free', members: [pb.username, pc.username] });
    ok('freie Gruppe A, B, C angelegt (A Admin, can_perms)', mk.s === 200 && mk.d.chat.can_perms === true, mk);
    const gid = mk.d.chat.id;
    await openC(a, gid); await openC(b, gid); await openC(c, gid);

    // --- Beschreibung ---------------------------------------------------------------------------------------------------
    await openInfo(a);
    ok('A: Gruppeninfo mit "Beschreibung hinzufügen" und Rechte-Schaltern', !!(await a.$('.ct-gi-desc-b')) && (await a.$$('.ct-gi-perm input')).length === 2);
    await a.click('.ct-gi-desc-b'); await a.fill('.ct-gi-desc-in', 'Wir planen die Wanderung im Mai.'); await a.click('.ct-gi-desc-save');
    ok('A: Beschreibung gespeichert (im Blatt)', !!(await until(() => a.$('.ct-gi-desc-t:text-is("Wir planen die Wanderung im Mai.")'), 10000)));
    // --- Gruppenbild -------------------------------------------------------------------------------------------------------
    await a.setInputFiles('.ct-gi-avfile', { name: 'bild.png', mimeType: 'image/png', buffer: png() });
    ok('A: Gruppenbild hochgeladen (Bild im Kopf der Gruppeninfo)', !!(await until(() => a.$('.ct-gi-avb img.hk-av-img'), 15000)));
    await shot(a, 'desktop-hell-info');
    await openInfo(b);
    ok('B: sieht Beschreibung und Bild (entschluesselt), keine Rechte-Schalter zum Aendern',
       !!(await until(() => b.$('.ct-gi-desc-t:text-is("Wir planen die Wanderung im Mai.")'), 15000)) && !!(await until(() => b.$('.ct-gi .hk-av-img'), 15000))
       && (await b.$$eval('.ct-gi-perm input', x => x.filter(i => !i.disabled).length).catch(() => 0)) === 0);
    await closeInfo(b);
    // --- Rechte: nur Admins schreiben ---------------------------------------------------------------------------------
    await a.click('.ct-gi-perm input[data-gi="perm_send"]');
    ok('B: Eingabefeld weg, Hinweis "nur Admins"', !!(await until(async () => (await b.isHidden('#ct-compose')) && await b.$('.ct-perm-note'), 15000)));
    await shot(b, 'desktop-hell-nurAdmins');
    await until(() => a.$('.ct-gi-perm input[data-gi="perm_send"]:not([disabled])'), 8000);
    await a.click('.ct-gi-perm input[data-gi="perm_send"]');
    ok('B: Eingabefeld wieder da', !!(await until(async () => !(await b.isHidden('#ct-compose')), 15000)));
    await closeInfo(a);
    // --- Anheften ----------------------------------------------------------------------------------------------------
    await until(async () => !(await b.isHidden('#ct-compose')), 5000);
    ok('B: schreibt "Treffpunkt Parkplatz"', !!(await send(b, 'Treffpunkt Parkplatz')));
    await until(() => a.$(rowSel('Treffpunkt Parkplatz')), 15000);
    const items = await msgMenu(a, 'Treffpunkt Parkplatz', 'Anheften');
    ok('A: Menue an fremder Nachricht: Anheften, Löschen (Admin), Sperren, Melden', ['Anheften', 'Löschen (Admin)', 'sperren', 'Melden'].every(x => items.some(t => t.indexOf(x) >= 0)), items);
    ok('A und C: Leiste "Angeheftet" mit der Nachricht', !!(await until(() => a.$('#ct-pins:not([hidden]) .ct-pin-p:has-text("Treffpunkt Parkplatz")'), 10000))
       && !!(await until(() => c.$('#ct-pins:not([hidden]) .ct-pin-p:has-text("Treffpunkt Parkplatz")'), 15000)));
    await shot(c, 'desktop-hell-pin');
    // --- Admin-Loeschen -----------------------------------------------------------------------------------------------
    await send(c, 'Unpassender Kommentar');
    await until(() => a.$(rowSel('Unpassender Kommentar')), 15000);
    await msgMenu(a, 'Unpassender Kommentar', 'Löschen (Admin)'); await confirmAsk(a);
    ok('A: an der Stelle "von einem Admin gelöscht"', !!(await until(() => a.$('.ct-tomb'), 10000)));
    ok('B: Grabstein live ("von einem Admin gelöscht"), Text weg', !!(await until(() => b.$('.ct-tomb'), 15000)) && !(await b.$(rowSel('Unpassender Kommentar'))));
    // Loesen
    await a.click('.ct-pin-x');
    ok('A: Anheftung geloest, Leiste weg', !!(await until(async () => await a.isHidden('#ct-pins'), 8000)));
    // --- Sperren ------------------------------------------------------------------------------------------------------
    await send(c, 'Hallo von C');
    await until(() => b.$(rowSel('Hallo von C')), 15000);
    await msgMenu(b, 'Hallo von C', 'sperren'); await confirmAsk(b);
    ok('B: Nachrichten von C eingeklappt ("gesperrten Person" + Anzeigen)', !!(await until(() => b.$('.ct-blockedmsg'), 10000)) && !(await b.$(rowSel('Hallo von C'))));
    await b.click('.ct-blockedmsg .btn');
    ok('B: "Anzeigen" klappt die Nachricht auf', !!(await until(() => b.$(rowSel('Hallo von C')), 5000)));
    const bl = await api(b, 'GET', '/api/chat/blocks');
    ok('B: Sperrliste nennt C mit Namen, ohne Anmeldenamen', (bl.d.blocks || []).some(x => x.name === nC && x.local) && JSON.stringify(bl.d).indexOf(U3) < 0, bl.d);
    for (const x of bl.d.blocks || []) await api(b, 'DELETE', '/api/chat/blocks?target=' + encodeURIComponent(x.target));
    // --- Melden -------------------------------------------------------------------------------------------------------
    await openC(b, gid);
    await until(() => b.$(rowSel('Hallo von C')), 10000);
    await msgMenu(b, 'Hallo von C', 'Melden');
    await b.waitForSelector('.ct-rep-text');
    ok('B: Melde-Dialog mit Grund und vorausgefuelltem Text', (await b.inputValue('.ct-rep-text')).indexOf('Hallo von C') >= 0);
    await b.selectOption('.ct-rep-reason', 'abuse');
    await shot(b, 'desktop-hell-melden');
    await b.click('.ct-rep-send');
    ok('B: Meldung abgeschickt (Dialog zu)', !!(await until(async () => !(await b.$('.ct-rep-text')), 8000)));

    // --- Handy-Breite, hell + dunkel -------------------------------------------------------------------------------------
    for (const scheme of ['light', 'dark']) {
      const m = await session(browser, U1, E.SAGA_PASS, { viewport: { width: 390, height: 844 }, mobile: true, scheme });
      await chatKeys(m, U1);
      await api(m, 'POST', '/api/chats/' + gid + '/pins/' + ((await api(m, 'GET', '/api/chats/' + gid + '/messages?device=x')).d.messages || [])
        .filter(x => x.kind === 'msg').slice(-1).map(x => x.id)[0]).catch(() => {});
      await openC(m, gid);
      await shot(m, 'handy-' + (scheme === 'light' ? 'hell' : 'dunkel') + '-chat');
      await openInfo(m);
      const w = await m.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
      ok('Handy ' + scheme + ': Gruppeninfo ohne waagrechtes Scrollen, Beschreibung sichtbar', w && !!(await m.$('.ct-gi-desc-t')));
      await shot(m, 'handy-' + (scheme === 'light' ? 'hell' : 'dunkel') + '-info');
      await m.context().close();
    }
    const d = await session(browser, U1, E.SAGA_PASS, { scheme: 'dark' });
    await chatKeys(d, U1); await openC(d, gid); await openInfo(d);
    await shot(d, 'desktop-dunkel-info');
    await d.context().close();
    ok('keine Seitenfehler', errs.length === 0, errs);
  } catch (e) {
    console.log('Fehler:', e && e.stack || e); res.push(false);
  } finally {
    await browser.close();
  }
  const n = res.filter(Boolean).length;
  console.log('\nBROWSER-GRUPPEN2 ' + n + '/' + res.length + ' - ' + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
