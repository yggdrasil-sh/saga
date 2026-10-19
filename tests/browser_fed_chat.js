// Browser-Test Foederation 2.3 E2/E3 (Playwright, headless Chrome mit Fake-Kamera/-Mikrofon) gegen ZWEI verbundene Test-Stacks:
// Direkt-Chat ueber Servergrenzen (Suche im Adressbuch, Nachrichtenanfrage, Annehmen, Antworten, Sicherheitsnummer v2,
// "ueberprueft") und Anruf (saga-call/v2) zwischen den Servern. Laeuft NICHT im Stack-Testlauf:
//   SAGA_A=http://host:portA SAGA_B=http://host:portB SAGA_USER_A=.. SAGA_PASS_A=.. SAGA_USER_B=.. SAGA_PASS_B=.. \
//   SAGA_FIND_B=<Teil des Anzeigenamens von USER_B> [PLAYWRIGHT=..] [SHOTS=<ordner>] node tests/browser_fed_chat.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const A = process.env.SAGA_A, B = process.env.SAGA_B, FIND = process.env.SAGA_FIND_B, SHOTS = process.env.SHOTS || os.tmpdir();
if (!A || !B || !FIND) { console.error('SAGA_A, SAGA_B, SAGA_USER_A/B, SAGA_PASS_A/B, SAGA_FIND_B setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function until(fn, ms = 20000, step = 300) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise((r) => setTimeout(r, step)); } return v; }
const shot = (p, n) => p.screenshot({ path: path.join(SHOTS, 'saga-fed-' + n + '.png') }).catch(() => {});

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
const RKF = (base, u) => path.join(os.tmpdir(), 'saga-browser-fedchat-rk-' + base.replace(/\W+/g, '_') + '-' + u + '.txt');
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
const st = (p) => p.evaluate(() => window.HKCalls && window.HKCalls.state());

(async () => {
  const browser = await PW.chromium.launch({ channel: process.env.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + new URL(A).origin + ',' + new URL(B).origin,
           '--autoplay-policy=no-user-gesture-required'] });
  try {
    const pa = await session(browser, A, process.env.SAGA_USER_A, process.env.SAGA_PASS_A);
    const pb = await session(browser, B, process.env.SAGA_USER_B, process.env.SAGA_PASS_B);
    await chatKeys(pa, 'a'); await chatKeys(pb, 'b');
    const ma = await until(async () => { const m = await pa.api('GET', '/api/chat/v2/me'); return !m.need && m; }, 15000);
    const mb = await until(async () => { const m = await pb.api('GET', '/api/chat/v2/me'); return !m.need && m; }, 15000);
    ok('Zertifikate v2 + signierte Geraete-Liste (beide)', ma && mb && ma.device_set_ok && mb.device_set_ok, { ma, mb });
    // --- A sucht B im Adressbuch und beginnt den Direkt-Chat ---------------------------------------------------------------
    await pa.click('#ct-new'); await pa.fill('.ct-people-q', FIND);
    await pa.waitForSelector('.ct-person.ct-remote', { timeout: 10000 });
    await shot(pa, 'chat-suche');
    await pa.click('.ct-person.ct-remote');
    await pa.waitForSelector('#ct-chat:not([hidden])', { timeout: 15000 });
    const title = await pa.textContent('#ct-title'), sub = await pa.textContent('#ct-sub');
    ok('A: Chat mit B offen, Herkunft im Kopf', title.includes(FIND) && sub.includes(new URL(B).host), { title, sub });
    await pa.fill('#ct-input', 'Hallo aus A – über Servergrenzen 👋'); await pa.press('#ct-input', 'Enter');
    ok('A: Nachricht gesendet und angezeigt', await until(async () => (await texts(pa)).includes('Hallo aus A – über Servergrenzen 👋')), await texts(pa));
    // --- B: Nachrichtenanfrage, annehmen, antworten ------------------------------------------------------------------------
    await pb.goto(B + '/chats'); await pb.waitForTimeout(2000);
    ok('B: Nachrichtenanfrage oben in der Liste', await until(async () => !!(await pb.$('#ct-req-h')), 15000));
    await shot(pb, 'anfrage-liste');
    await pb.click('.ct-item-b');
    await pb.waitForSelector('#ct-chat:not([hidden])');
    ok('B: Nachricht der Anfrage lesbar (entschluesselt)', await until(async () => (await texts(pb)).includes('Hallo aus A – über Servergrenzen 👋')), await texts(pb));
    ok('B: Anfrage-Leiste statt Eingabefeld', await pb.isVisible('.ct-req-accept') && !(await pb.isVisible('#ct-compose')));
    await shot(pb, 'anfrage');
    await pb.click('.ct-req-accept');
    ok('B: angenommen -> Eingabefeld da', await until(() => pb.isVisible('#ct-compose'), 10000));
    await pb.fill('#ct-input', 'Hallo zurück aus B'); await pb.press('#ct-input', 'Enter');
    ok('A: Antwort angekommen, Echtheit bestaetigt', await until(async () => (await texts(pa)).includes('Hallo zurück aus B'), 20000)
      && !(await pa.$('#ct-msgs .ct-unverified')), await texts(pa));
    // Heim = A: die eigene Antwort darf bei B nicht auf "wird zugestellt" (⏳) haengen bleiben (Fehler 2.3.0)
    ok('B: eigene Antwort zugestellt (kein ⏳)', await until(async () => (await texts(pb)).includes('Hallo zurück aus B') && !(await pb.$('#ct-msgs .ct-pend')), 10000));
    // --- Sicherheitsnummer v2 auf beiden Seiten gleich, "ueberprueft" --------------------------------------------------------
    async function safetyOf(p) {
      await p.click('#ct-more'); await p.click('button.menu-item:has-text("Sicherheitsnummer")');
      await p.waitForSelector('#ct-safety'); const n = (await p.textContent('#ct-safety')).replace(/\s+/g, ''); return n;
    }
    const sa = await safetyOf(pa), sb = await safetyOf(pb);
    ok('Sicherheitsnummer v2 auf beiden Seiten gleich', sa && sa.length === 60 && sa === sb, { sa, sb });
    await pa.click('.ct-verify'); await pa.waitForTimeout(800);
    await pb.keyboard.press('Escape');
    ok('A: Chat-Kopf zeigt "überprüft"', await until(() => pa.isVisible('#ct-sub .ct-verified'), 5000));
    await shot(pa, 'chat-ueberprueft');
    // --- Anruf A -> B ueber Servergrenzen (Fake-Medien) ---------------------------------------------------------------------
    ok('A: Anruf-Knoepfe im foederierten Direkt-Chat', await until(() => pa.isVisible('#ct-call-a'), 5000));
    await pa.click('#ct-call-a');
    ok('B: es klingelt', await until(() => pb.$('.cl-ring'), 20000));
    await shot(pb, 'anruf-klingelt');
    await pb.click('.cl-ring .cl-big');
    const ca = await until(async () => { const s = await st(pa); return s && s.connected && s; }, 30000);
    const cb = await until(async () => { const s = await st(pb); return s && s.connected && s; }, 30000);
    ok('Anruf verbunden (beide Seiten), DTLS-Fingerabdruck geprueft', ca && cb && ca.verified && cb.verified, { ca, cb });
    await pa.waitForTimeout(2500);
    await shot(pa, 'anruf');
    await pa.click('.cl-red');
    ok('Auflegen: beide Seiten beendet', await until(async () => !(await st(pa)) && !(await st(pb)), 15000));
    ok('Anruf-Eintrag im Chat (beide)', await until(async () => !!(await pa.$('#ct-msgs .ct-call')) && !!(await pb.$('#ct-msgs .ct-call')), 15000));
    ok('keine JS-Fehler (A)', !(pa.errs || []).length, pa.errs);
    ok('keine JS-Fehler (B)', !(pb.errs || []).length, pb.errs);
  } catch (e) {
    console.error(e); res.push(false);
  } finally {
    await browser.close();
  }
  console.log(`\n${res.filter(Boolean).length}/${res.length} OK – ` + (res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
