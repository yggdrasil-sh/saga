// Browser-Test Foederation 2.3 E4-E6 (Playwright, headless Chrome) gegen ZWEI verbundene Test-Stacks: fremde Datenbank
// oeffnen und bearbeiten (Titel, Haekchen, neue Zeile), Titelbild in eine fremde Seite hochladen (liegt beim Eigentuemer),
// Anwesenheit ueber Servergrenzen, Versionsverlauf fuer Bearbeiter, Gruppen-Chat mit einer Person des anderen Servers
// (Anlegen ueber die Personensuche, Anfrage annehmen, Nachrichten in beide Richtungen, Systemzeile mit Herkunft).
// Laeuft NICHT im Stack-Testlauf:
//   SAGA_A=http://host:portA SAGA_B=http://host:portB SAGA_USER_A=.. SAGA_PASS_A=.. SAGA_USER_B=.. SAGA_PASS_B=.. \
//   SAGA_SHARE_B=@name@hostB SAGA_FIND_B=<Teil des Anzeigenamens von USER_B> [PLAYWRIGHT=..] [SHOTS=<ordner>] node tests/browser_fed_share.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os'), zlib = require('zlib');
const A = process.env.SAGA_A, B = process.env.SAGA_B, FIND = process.env.SAGA_FIND_B, SHARE = process.env.SAGA_SHARE_B, SHOTS = process.env.SHOTS || os.tmpdir();
if (!A || !B || !FIND || !SHARE) { console.error('SAGA_A, SAGA_B, SAGA_USER_A/B, SAGA_PASS_A/B, SAGA_SHARE_B, SAGA_FIND_B setzen'); process.exit(2); }
const res = [], RUN = Date.now().toString(36);          // eindeutige Titel je Lauf (Stacks werden nicht geleert)
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function until(fn, ms = 20000, step = 300) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise((r) => setTimeout(r, step)); } return v; }
const shot = (p, n) => p.screenshot({ path: path.join(SHOTS, 'saga-fedshare-' + n + '.png') }).catch(() => {});

function png(w, h) {                                  // einfarbiges PNG ohne Abhaengigkeiten
  const crc = (b) => { let c, t = []; for (let n = 0; n < 256; n++) { c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    let x = 0xffffffff; for (const v of b) x = t[(x ^ v) & 255] ^ (x >>> 8); return (x ^ 0xffffffff) >>> 0; };
  const chunk = (typ, data) => { const l = Buffer.alloc(4); l.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(typ), data]);
    const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([l, td, c]); };
  const ih = Buffer.alloc(13); ih.writeUInt32BE(w, 0); ih.writeUInt32BE(h, 4); ih[8] = 8; ih[9] = 2;
  const raw = Buffer.alloc((w * 3 + 1) * h); for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const o = y * (w * 3 + 1) + 1 + x * 3; raw[o] = 40; raw[o + 1] = 120; raw[o + 2] = 200; }
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ih), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

async function session(browser, base, user, pass) {
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 860 }, locale: 'de-DE' });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => { p.errs = (p.errs || []).concat(String(e)); });
  await p.goto(base + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', pass);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(1000);
  p.base = base;
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'X-Saga-Chat': '2', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, u, b]);
  return p;
}
const RKF = (base, u) => path.join(os.tmpdir(), 'saga-browser-fedshare-rk-' + base.replace(/\W+/g, '_') + '-' + u + '.txt');
async function chatKeys(q, user) {
  await q.goto(q.base + '/chats'); await q.waitForTimeout(2500);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim(); fs.writeFileSync(RKF(q.base, user), rk, { mode: 0o600 });
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 60000 });
  } else if (await q.$('.ct-rk-go') && fs.existsSync(RKF(q.base, user))) {
    await q.click('.ct-rk-go'); await q.fill('.ct-rk-in', fs.readFileSync(RKF(q.base, user), 'utf8').trim());
    await q.click('.ct-rk-ok'); await q.waitForSelector('.ct-rk-ok', { state: 'detached', timeout: 60000 });
  }
  await q.waitForTimeout(2000);
}
const texts = (p) => p.$$eval('#ct-msgs .ct-msg .ct-text', (n) => n.map((x) => x.textContent));

(async () => {
  const browser = await PW.chromium.launch({ channel: process.env.PW_CHANNEL || undefined, headless: true,
    args: ['--unsafely-treat-insecure-origin-as-secure=' + new URL(A).origin + ',' + new URL(B).origin] });
  const tmpPng = path.join(os.tmpdir(), 'saga-fedshare-cover.png');
  fs.writeFileSync(tmpPng, png(320, 120));
  try {
    const pa = await session(browser, A, process.env.SAGA_USER_A, process.env.SAGA_PASS_A);
    const pb = await session(browser, B, process.env.SAGA_USER_B, process.env.SAGA_PASS_B);
    // --- A: Seite mit Datenbank anlegen und mit B teilen (Bearbeiten) ------------------------------------------------------
    const P = (await pa.api('POST', '/api/notes', { title: 'Browser-Projekt ' + RUN })).id;
    const D = (await pa.api('POST', '/api/notes', { title: 'Browser-Aufgaben ' + RUN, kind: 'database', parent_id: P })).id;
    const sch = (await pa.api('GET', '/api/notes/' + D)).schema;
    sch.props.push({ id: 'ok', name: 'Erledigt', type: 'checkbox' });
    await pa.api('PATCH', '/api/notes/' + D, { schema: sch });
    const rows = [];
    for (const n of ['Erste', 'Zweite', 'Dritte']) rows.push((await pa.api('POST', '/api/notes', { title: n, parent_id: D })).id);
    const sh = await pa.api('PUT', '/api/notes/' + P + '/shares', { type: 'remote', target: SHARE, role: 'edit' });
    ok('A: Seite mit Datenbank an B geteilt', !sh.error, sh);
    const items = await until(async () => { const r = await pb.api('GET', '/api/fed/shared'); const it = (r.items || []); return it.some((x) => x.title === 'Browser-Aufgaben ' + RUN) && it; }, 20000);
    const rD = (items || []).find((x) => x.title === 'Browser-Aufgaben ' + RUN).id, rP = (items || []).find((x) => x.title === 'Browser-Projekt ' + RUN).id;
    ok('B: Datenbank in "Mit mir geteilt" (negative ID, Faehigkeit db)', rD < 0 && ((items.find((x) => x.id === rD).remote || {}).caps || []).includes('db'), { rD });
    // --- B: fremde Datenbank oeffnen und bearbeiten ------------------------------------------------------------------------
    await pb.goto(B + '/notes#' + rD);
    ok('B: fremde Datenbank als Tabelle (3 Zeilen)', await until(async () => (await pb.$$('tr.db-row')).length === 3, 20000), (await pb.$$('tr.db-row')).length);
    await shot(pb, 'fremde-db');
    await pb.locator('tr.db-row .db-title-in').nth(0).click(); await pb.keyboard.press('End'); await pb.keyboard.type(' (von B)');
    await pb.evaluate(() => document.activeElement.blur()); await pb.waitForTimeout(1200);
    const t0 = await until(async () => { const r = await pa.api('GET', '/api/notes/' + D + '/rows'); return (r.rows || []).find((x) => /\(von B\)$/.test(x.title)); }, 15000);
    ok('A: Zeilentitel von B gespeichert', !!t0, t0);
    await pb.locator('tr.db-row input[type=checkbox]').nth(1).check(); await pb.waitForTimeout(1200);
    const c1 = await until(async () => { const r = await pa.api('GET', '/api/notes/' + D + '/rows'); return (r.rows || []).some((x) => (x.props || {}).ok === true); }, 15000);
    ok('A: Haekchen von B gespeichert', !!c1);
    await pb.click('button.db-new-row'); await pb.waitForTimeout(1500);
    await pb.keyboard.type('Neu von B'); await pb.keyboard.press('Enter'); await pb.waitForTimeout(1200);
    const n1 = await until(async () => { const r = await pa.api('GET', '/api/notes/' + D + '/rows'); return (r.rows || []).some((x) => x.title === 'Neu von B'); }, 15000);
    ok('A: neue Zeile von B (Unterseite der Datenbank)', !!n1);
    // --- B: Titelbild in die fremde Seite hochladen; A sieht B auf der Seite ---------------------------------------------
    await pb.goto(B + '/notes#' + rP); await pb.waitForTimeout(2500);
    await pb.hover('#pg-title').catch(() => {});
    const [fc] = await Promise.all([pb.waitForEvent('filechooser', { timeout: 10000 }), pb.evaluate(() => document.getElementById('pg-addcover').click())]);
    await fc.setFiles(tmpPng);
    const cov = await until(async () => { const n = await pa.api('GET', '/api/notes/' + P); return n.cover && n.cover.startsWith('/files/') && n.cover; }, 20000);
    ok('A: Titelbild liegt beim Eigentuemer (/files/…)', !!cov, cov);
    ok('B: Titelbild angezeigt (ueber den eigenen Server geladen)', await until(() => pb.evaluate(() => { const i = document.getElementById('pg-cover-img'); return !!i && i.complete && i.naturalWidth > 100; }), 15000));
    await shot(pb, 'fremde-seite-titelbild');
    await pa.goto(A + '/notes#' + P);
    ok('A: Anwesenheit - Bert (B) ist auf der Seite', await until(async () => !!(await pa.$('#pg-presence')), 30000));
    await shot(pa, 'anwesenheit');
    await pb.click('#pg-more'); await pb.waitForTimeout(300);
    ok('B (Bearbeiten): Versionsverlauf im Seitenmenue', await pb.isVisible('button.menu-item:has-text("Versionsverlauf")'));
    await pb.keyboard.press('Escape');
    // --- Gruppen-Chat mit einer Person des anderen Servers ------------------------------------------------------------------
    await chatKeys(pa, 'a'); await chatKeys(pb, 'b');
    await until(async () => { const m = await pa.api('GET', '/api/chat/v2/me'); return !m.need && m; }, 15000);
    await until(async () => { const m = await pb.api('GET', '/api/chat/v2/me'); return !m.need && m; }, 15000);
    await pa.click('#ct-new'); await pa.click('.ct-free-toggle'); await pa.fill('.ct-people-q', FIND);
    await pa.waitForSelector('.ct-person.ct-remote', { timeout: 10000 }); await pa.click('.ct-person.ct-remote');
    await pa.fill('.ct-free input.hk-in', 'Gruppe über Servergrenzen ' + RUN);
    await pa.click('.ct-free-go');
    await pa.waitForSelector('#ct-chat:not([hidden])', { timeout: 15000 });
    ok('A: Gruppe angelegt (Name verschluesselt, Mitglied mit Herkunft im Kopf)', await until(async () =>
      (await pa.textContent('#ct-title')).includes('Gruppe über Servergrenzen ' + RUN) && (await pa.textContent('#ct-sub')).includes(new URL(B).host), 15000),
      [await pa.textContent('#ct-title'), await pa.textContent('#ct-sub')]);
    await pa.fill('#ct-input', 'Hallo Gruppe'); await pa.press('#ct-input', 'Enter');
    ok('A: Nachricht in der Gruppe', await until(async () => (await texts(pa)).includes('Hallo Gruppe')));
    await shot(pa, 'gruppe-a');
    await pb.goto(B + '/chats'); await pb.waitForTimeout(2000);
    ok('B: Einladung als Anfrage', await until(async () => !!(await pb.$('#ct-req-h')), 20000));
    await pb.click('.ct-item-b');
    await pb.waitForSelector('#ct-chat:not([hidden])');
    ok('B: Gruppenname und Nachricht lesbar', await until(async () => (await texts(pb)).includes('Hallo Gruppe') && (await pb.textContent('#ct-title')).includes('Gruppe über Servergrenzen ' + RUN), 20000),
      [await texts(pb), await pb.textContent('#ct-title')]);
    ok('B: Systemzeile mit Herkunft', await until(async () => (await pb.$$eval('#ct-msgs .ct-sys', (n) => n.map((x) => x.textContent))).some((x) => x.includes(new URL(A).host)), 10000));
    await shot(pb, 'gruppe-anfrage');
    await pb.click('.ct-req-accept');
    ok('B: angenommen -> Eingabefeld', await until(() => pb.isVisible('#ct-compose'), 10000));
    await pb.fill('#ct-input', 'Hallo zurück aus B'); await pb.press('#ct-input', 'Enter');
    ok('A: Antwort aus B angekommen, Echtheit bestaetigt', await until(async () => (await texts(pa)).includes('Hallo zurück aus B'), 20000)
      && !(await pa.$('#ct-msgs .ct-unverified')), await texts(pa));
    await shot(pa, 'gruppe-antwort');
    ok('keine JS-Fehler (A)', !(pa.errs || []).length, pa.errs);
    ok('keine JS-Fehler (B)', !(pb.errs || []).length, pb.errs);
  } catch (e) {
    console.error(e); res.push(false);
  } finally {
    await browser.close();
    try { fs.unlinkSync(tmpPng); } catch (e) { /* weg */ }
  }
  console.log(`\n${res.filter(Boolean).length}/${res.length} OK – ` + (res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
