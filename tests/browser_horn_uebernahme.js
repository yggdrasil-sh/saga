// Browser-Ende-zu-Ende "Hierher übernehmen" bei einem 1:1-Anruf ÜBER EINEN KNOTEN (calls.horn + calls.horn_all,
// eingebauter oder externer Knoten bereit). Fund: iPhone -> Mac - das neue Gerät meldete das alte nur beim Server ab,
// das alte telefonierte weiter. Geprüft: A ruft B an (Knoten), das zweite Gerät A2 übernimmt aus der Leiste; danach
// ist A2 im Anruf (Ton an, mit dem Knoten verbunden), A hat aufgelegt und B ist noch verbunden - mit A2 statt A.
//   SAGA_BASE=... SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... node tests/browser_horn_uebernahme.js
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_USER2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function until(fn, ms = 20000, step = 150) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
let br;
async function session(user, pass, name) {
  const ctx = await br.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.on('pageerror', e => console.log('  [pageerror ' + name + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + name + '] ' + m.text()));
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
const horn = p => p.evaluate(() => window.HKCalls && window.HKCalls.horn && window.HKCalls.horn());
const room = p => p.evaluate(() => window.HKCalls && window.HKCalls.room());
const dev = p => p.evaluate(() => window.HKChat.state().device);

(async () => {
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  br = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] });
  try {
    const a = await session(U1, E.SAGA_PASS, 'A'), b = await session(U2, E.SAGA_PASS2, 'B');
    await chatKeys(a, U1); await chatKeys(b, U2);
    const a2 = await session(U1, E.SAGA_PASS, 'A2');
    await chatKeys(a2, U1);
    const dA = await dev(a), dA2 = await dev(a2);
    ok('zwei Geräte von A und eins von B mit Chat-Schlüsseln', dA && dA2 && dA !== dA2 && await dev(b));

    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1000);
    await b.goto(BASE + '/chats'); await a2.goto(BASE + '/chats'); await a2.waitForTimeout(1500);
    await a.click('#ct-call-v');
    await b.waitForSelector('.cl-ring', { timeout: 20000 }); await b.click('.cl-ring .cl-big');
    const h1 = await until(async () => { const x = await horn(a), y = await horn(b); return x && x.up && y && y.up && [x, y]; }, 30000);
    ok('1:1 A <-> B läuft über den Knoten (beide verbunden)', h1, [await horn(a), await horn(b)]);

    await until(() => a2.evaluate(() => !!document.querySelector('.cl-run .cl-run-go')), 20000, 250);
    ok('A2: Leiste mit "Hierher übernehmen"', await a2.$('.cl-run .cl-run-go'));
    const t0 = Date.now();
    await a2.click('.cl-run .cl-run-go');
    const r2 = await until(async () => { const r = await room(a2), h = await horn(a2); return r && !r.ho && !r.companion && r.mic && h && h.up && r; }, 30000);
    ok(`A2 hat übernommen: Ton an, mit dem Knoten verbunden (${Date.now() - t0} ms)`, r2, [await room(a2), await horn(a2)]);
    const gone = await until(async () => !(await room(a)), 15000, 250);
    ok('A (altes Gerät) hat aufgelegt', gone, await room(a));
    const rb = await until(async () => { const r = await room(b), h = await horn(b); return r && h && h.up && r.legs.includes(dA2) && !r.legs.includes(dA) && r; }, 15000, 250);
    ok('B ist weiter verbunden - mit A2 statt A', rb, await room(b));
    const hint = await a.evaluate(() => document.body.innerText.includes('übernommen'));
    ok('A zeigt "Anruf auf … übernommen"', hint);
  } catch (e) {
    ok('Ablauf', false, String(e && e.stack || e));
  } finally {
    await br.close();
    const n = res.filter(Boolean).length;
    console.log(`\nHORN-UEBERNAHME ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
    process.exit(n === res.length ? 0 : 1);
  }
})();
