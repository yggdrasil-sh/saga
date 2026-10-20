// Browser-Ende-zu-Ende "mehrere Geraete im Anruf" (2.4, Testrunde W9) - wie die Testrunde: ALLE Geraete als Kontexte in
// EINEM Chromium auf demselben Rechner (Fake-Kamera/-Mikrofon), dazu weitere offene Saga-Tabs mit Live-Kanal.
//   SAGA_BASE=... SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... [EXTRA_TABS=4] [SEPARATE=1] node tests/browser_geraete.js
// Geprueft: Kopplungsanfrage eines neuen Geraets liegt UEBER dem Vollbild-Anruf; "Hinzufügen" (Begleitgeraet) verbindet
// sich, Statuszeile verschwindet, sobald Medien fliessen; "Hierher übernehmen" klappt, der Anruf laeuft ohne Abbruch weiter.
// SEPARATE=1: je Geraet ein eigener Browser (Gegenprobe wie tests/browser_raum.js).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_USER2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '', EXTRA = +(E.EXTRA_TABS || 4), SEP = E.SEPARATE === '1';
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, 'geraete-' + name + '.png') }); } }
const BROWSERS = [];
let shared = null;
async function browser() {
  if (!SEP && shared) return shared;
  const origin = new URL(BASE).origin;
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin,
           '--autoplay-policy=no-user-gesture-required'] });
  BROWSERS.push(br);
  if (!SEP) shared = br;
  return br;
}
async function session(user, pass, name) {
  const ctx = await (await browser()).newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.__name = name;
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
async function liveOk(...ps) { for (const p of ps) await until(() => p.evaluate(() => !!(window.HKLive && window.HKLive.state().sse)), 15000, 200); }
const room = p => p.evaluate(() => window.HKCalls && window.HKCalls.room());
const dev = p => p.evaluate(() => window.HKChat.state().device);
async function until(fn, ms = 20000, step = 100) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const allConn = (r, n) => r && r.links.length === n && r.links.every(l => l.connected) && r;
const statusLine = p => p.evaluate(() => { const s = document.querySelector('.cl-win .cl-status'); return s && !s.hidden ? s.textContent : ''; });

(async () => {
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  try {
    const a = await session(U1, E.SAGA_PASS, 'A'), b = await session(U2, E.SAGA_PASS2, 'B');
    await chatKeys(a, U1); await chatKeys(b, U2);
    const a2 = await session(U1, E.SAGA_PASS, 'A2');
    await chatKeys(a2, U1);
    const extra = [];
    for (let i = 0; i < EXTRA; i++) { const x = await session(i % 2 ? U2 : U1, i % 2 ? E.SAGA_PASS2 : E.SAGA_PASS, 'X' + i); await x.goto(BASE + '/'); extra.push(x); }
    // Web-Runde 2, H4: meldet der Server beim Laden einmal (noch) keine Identitaet, bleibt der Tab bei den vorhandenen
    // Schluesseln (kein "Chats einrichten", nichts vergessen)
    let once = true;
    await a2.route('**/api/chat/me?*', async route => {
      if (!once) return route.continue();
      once = false;
      const r = await route.fetch(); const j = await r.json(); j.identity = null;
      return route.fulfill({ response: r, body: JSON.stringify(j) });
    });
    await a2.goto(BASE + '/chats'); 
    const h4 = await until(() => a2.evaluate(() => (window.HKChat && window.HKChat.state().ready) ? 'ready' : (document.querySelector('.ct-setup-go') ? 'setup' : null)), 15000, 200);
    ok('H4: kurzzeitig ohne Identitaet beim Laden -> kein "Chats einrichten", Schluessel bleiben', h4 === 'ready', h4);
    await a2.unroute('**/api/chat/me?*');
    const dA = await dev(a), dA2 = await dev(a2), dB = await dev(b);
    ok(`drei Geraete (A, A2, B) ${SEP ? 'je in eigenem Browser' : 'in EINEM Browser'} + ${EXTRA} weitere Saga-Tabs`, dA && dA2 && dB && dA !== dA2);

    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1000);
    await b.goto(BASE + '/chats'); await a2.goto(BASE + '/chats'); await liveOk(a, b, a2, ...extra);
    await a.click('#ct-call-v');
    await b.waitForSelector('.cl-ring', { timeout: 15000 }); await b.click('.cl-ring .cl-big');
    const r1 = await until(async () => allConn(await room(a), 1), 20000);
    ok('Videoanruf A <-> B verbunden', r1 && r1.links[0].device === dB, r1);

    // --- Kopplungsanfrage eines neuen Geraets waehrend des Vollbild-Anrufs ------------------------------------------
    const n3 = await session(U1, E.SAGA_PASS, 'A3');
    await n3.goto(BASE + '/chats'); await n3.waitForTimeout(2500);
    const go = await n3.$('.ct-link-go');
    if (go) {
      await go.click();
      const top = await until(() => a.evaluate(() => {
        const ov = document.querySelector('.ct-ov.ct-ov-top'); if (!ov) return null;
        const r = ov.querySelector('.hk-dialog').getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return { inDialog: !!(hit && hit.closest('.ct-ov')), callOpen: !!document.querySelector('.cl-win') };
      }), 20000, 250);
      ok('Kopplungsanfrage liegt ueber dem Vollbild-Anruf (sichtbar, anklickbar)', top && top.inDialog && top.callOpen, top);
      await shot(a, '1-kopplung-ueber-anruf');
      await a.click('.ct-ov-top .ct-approve');
      ok('Bestaetigt: die Anfrage schliesst sich auch auf dem anderen eigenen Geraet (A2)',
         await until(() => a2.evaluate(() => !document.querySelector('.ct-ov-top')), 10000, 200));
      ok('Neues Geraet A3 waehrend des Anrufs gekoppelt', await until(() => n3.evaluate(() => !!(window.HKChat && window.HKChat.state().ready)), 30000, 250));
    } else ok('Neues Geraet: Knopf "Mit anderem Gerät bestätigen"', false, await n3.textContent('body').catch(() => ''));
    const dA3 = await dev(n3);

    // --- Hinzufuegen (Begleitgeraet) mit dem eben gekoppelten Geraet ------------------------------------------------
    const bar = await until(async () => n3.evaluate(() => { const x = document.querySelector('.cl-run'); return x && x.textContent; }), 15000);
    ok('A3: Leiste mit Hinzufügen und Hierher übernehmen', bar && /Hinzufügen/.test(bar) && /Hierher übernehmen/.test(bar), bar);
    let t0 = Date.now();
    await n3.click('.cl-run .cl-pill:not(.cl-run-go)');
    const r2 = await until(async () => allConn(await room(n3), 2), 30000);
    ok(`Hinzufügen (in diesem Anruf gekoppeltes Geraet): mit B und A verbunden (${Date.now() - t0} ms)`, r2 && r2.companion, await room(n3));
    const vid = await until(() => n3.evaluate(() => [...document.querySelectorAll('.cl-win video')].some(v => v.videoWidth > 0 && v.srcObject)), 10000);
    ok('Begleitgeraet: Bild des Gegenuebers kommt an', vid);
    const st2 = await until(async () => (await statusLine(n3)) === '' && 'leer', 8000);
    ok('Begleitgeraet: keine haengende Zeile "Verbinde …"', st2, await statusLine(n3));
    await shot(n3, '2-begleitgeraet');

    // --- Hierher uebernehmen -----------------------------------------------------------------------------------------
    t0 = Date.now();
    await n3.click('.cl-take');
    const r3 = await until(async () => { const r = await room(n3); return r && !r.companion && r.mic && r.spkOut && allConn(r, 1); }, 25000);
    ok(`Hierher übernehmen: A3 ist Hauptgeraet, verbunden mit B (${Date.now() - t0} ms)`, r3, await room(n3));
    ok('M1: Kamera nach der Uebernahme wie beim abgebenden Geraet (an)', await until(async () => { const r = await room(n3); return r && r.cam; }, 10000), await room(n3));
    const gone = await until(async () => !(await room(a)), 15000);
    ok('A hat den Anruf abgegeben (kein Abbruch bei B)', gone && (await until(async () => { const r = allConn(await room(b), 1); return r && r.links[0].device === dA3 && r; }, 10000)), await room(b));
    await shot(n3, '3-uebernommen');
    await b.click('.cl-ctl .cl-red');
    await until(async () => !(await room(n3)), 10000);
    await n3.context().close();

    // --- Hierher uebernehmen direkt aus der Leiste (ohne vorher Begleitgeraet) ----------------------------------------
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await liveOk(a);
    await a.click('#ct-call-a');
    await b.waitForSelector('.cl-ring', { timeout: 15000 }); await b.click('.cl-ring .cl-big');
    await until(async () => allConn(await room(a), 1), 20000);
    await until(async () => a2.evaluate(() => !!document.querySelector('.cl-run .cl-run-go')), 15000);
    let t1 = Date.now();
    await a2.click('.cl-run .cl-run-go');
    const r4 = await until(async () => { const r = await room(a2); return r && !r.companion && r.mic && allConn(r, 1); }, 25000);
    ok(`Leiste "Hierher übernehmen": klappt ohne Fehlermeldung (${Date.now() - t1} ms)`, r4 && !(await a2.$('.cl-err')), await room(a2));
    ok('M1: ... Audioanruf: Kamera bleibt aus', r4 && !r4.cam, r4);
    ok('... A ist danach nicht mehr im Anruf, B weiter verbunden', await until(async () => !(await room(a)), 15000) && allConn(await room(b), 1));
    await b.click('.cl-ctl .cl-red').catch(() => {});
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e));
    for (const br of BROWSERS) for (const ctx of br.contexts()) for (const p of ctx.pages()) {
      const t = await p.evaluate(() => { const s = document.querySelector('.cl-status'); return [location.href, s && s.textContent, JSON.stringify(window.HKCalls && window.HKCalls.room())]; }).catch(() => null);
      console.log('  [' + p.__name + '] ' + JSON.stringify(t).slice(0, 1500)); await shot(p, 'fehler-' + p.__name).catch(() => {});
    }
  } finally {
    for (const br of BROWSERS) await br.close().catch(() => {});
  }
  console.log(`\nBROWSER-GERAETE ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
