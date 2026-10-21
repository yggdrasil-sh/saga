// 2.5 (Testrunde Web, Nr. 173): 1:1-Anruf klingelt beim Gegenueber zuverlaessig - auch wenn die Person die Chat-Seite
// gerade (neu) laedt und der Anruf kommt, bevor ihr Live-Kanal steht, und bei mehreren Anrufen kurz hintereinander
// (Abstand 3-8 s, abwechselnd abgelehnt, aufgelegt, nicht angenommen und angenommen).
//   SAGA_BASE=... SAGA_USER/PASS SAGA_USER2/PASS2 [PLAYWRIGHT=...] [RUNDEN=6] node tests/browser_klingeln.js
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '';
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, name + '.png') }); } }
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 20000, step = 200) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await sleep(step); } return v; }

async function session(browser, user, pass) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.on('pageerror', e => console.log('  [pageerror ' + user + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + user + '] ' + m.text()));
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

(async () => {
  const origin = new URL(BASE).origin;
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin,
           '--autoplay-policy=no-user-gesture-required'] });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2, N = +(E.RUNDEN || 6);
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2);
    await chatKeys(a, U1); await chatKeys(b, U2);
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
    // Live-Kanal von B beim Aufbau kuenstlich verzoegern: so kommt der Anruf sicher in der Luecke zwischen dem Laden der
    // Seite und dem ersten hello an (Testrunde: Jonas laedt /chats, Lena ruft gleich danach an)
    await b.route('**/api/events*', async r => { await sleep(2500); r.continue().catch(() => {}); });
    const miss = [], times = [], gap = [];
    // Ablauf je Runde: B laedt /chats (jede zweite Runde unmittelbar vor dem Anruf - der Anruf kommt, bevor der Live-Kanal
    // steht), A ruft an, B muss klingeln; dann abwechselnd: B lehnt ab / A legt auf / B nimmt an und A legt auf
    for (let i = 0; i < N; i++) {
      const reload = i % 2 === 0;
      if (reload) {
        // nach der ersten Abfrage laufender Anrufe (calls.js fragt kurz nach dem Laden einmal), aber vor dem Live-Kanal
        const first = b.waitForResponse(r => /\/api\/calls\/active/.test(r.url()), { timeout: 15000 }).catch(() => null);
        await b.goto(BASE + '/chats'); await first; await b.waitForTimeout(200);
        gap.push(await b.evaluate(() => !window.HKLive.state().sse));
      }
      const t0 = Date.now();
      await a.click('#ct-call-v');
      const rang = await until(() => b.isVisible('.cl-ring'), 15000);
      const st = await a.evaluate(() => { const s = document.querySelector('.cl-win .cl-st, .cl-win'); return s ? s.textContent.slice(0, 80) : ''; });
      times.push(rang ? Date.now() - t0 : -1);
      if (!rang) { miss.push({ runde: i + 1, reload, anrufer: st }); await shot(b, 'klingeln-' + (i + 1) + '-fehlt'); }
      const how = i % 3;
      if (rang && how === 0) await b.click('.cl-ring .cl-red');
      else if (rang && how === 2) {
        await b.click('.cl-ring .cl-big');
        await until(() => b.evaluate(() => { const s = window.HKCalls && window.HKCalls.state(); return s && s.connected; }), 20000);
        await a.click('.cl-ctl .cl-red').catch(() => {});
      } else await a.click('.cl-ctl .cl-red').catch(() => {});
      await until(() => a.evaluate(() => !window.HKCalls.state()), 10000);
      await until(async () => !(await b.isVisible('.cl-ring')) && await b.evaluate(() => !window.HKCalls.state()), 10000);
      // Bremse des Servers: hoechstens 3 Anrufe je Minute an dieselbe Person - Abstand so, dass sie nicht greift
      await sleep(Math.max(3000 + Math.floor(Math.random() * 5000), 21000 - (Date.now() - t0)));
    }
    console.log('  Klingeln nach ms: ' + JSON.stringify(times));
    ok('Anrufe kamen in der Luecke vor dem Live-Kanal an (Testaufbau)', gap.length && gap.every(Boolean), gap);
    ok(`Jeder 1:1-Anruf klingelt beim Gegenueber (${N - miss.length}/${N}, auch direkt nach dem Laden der Seite)`, !miss.length, miss);
    // Seite geladen, waehrend es schon klingelt: sofort sichtbar
    await a.click('#ct-call-a'); await until(() => b.isVisible('.cl-ring'), 15000);
    await b.goto(BASE + '/'); const t1 = Date.now();
    ok('Neu geladene Seite zeigt den klingelnden Anruf sofort (< 4 s)', await until(() => b.isVisible('.cl-ring'), 4000), Date.now() - t1);
    await a.click('.cl-ctl .cl-red').catch(e => console.log('  Auflegen: ' + e));
    if (E.DEBUG) console.log('  A nach Auflegen: ' + JSON.stringify(await a.evaluate(() => window.HKCalls.state())));
    ok('Nach dem Auflegen klingelt nichts mehr', await until(async () => !(await b.isVisible('.cl-ring')), 8000));
  } catch (e) { ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e).slice(0, 600)); }
  finally { await browser.close(); }
  const n = res.filter(Boolean).length;
  console.log(`\nBROWSER-KLINGELN ${n}/${res.length} - ` + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
