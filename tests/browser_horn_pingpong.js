// Browser-Ende-zu-Ende "Hin und Her" (2.5 Anruf-Uebergabe Phase 1, docs/anruf-uebergabe-hinzufuegen.md T1-T3) bei einem
// 1:1-Anruf UEBER EINEN KNOTEN (calls.horn + calls.horn_all, Knoten bereit). Laeuft nur mit echtem Knoten (Labor bzw. ein
// Server mit eingebautem Knoten); im Container ohne Go/Knoten nicht.
// Geprueft:
//  - A (zwei Geraete A1/A2) <-> B; RUNDEN (Standard 10) Mal "Hierher uebernehmen" aus der Leiste im Wechsel, Pausen 5 s bzw.
//    1 s: jede Uebernahme <= 3 s (Ton an, mit dem Knoten verbunden), das alte Geraet legt auf, B bleibt verbunden und hoert
//    das neue Geraet (Pegel), nie zwei eigene Geraete mit offenem Mikrofon, kein 401 "entfernt" (Sperre der alten Kennung).
//  - Schneller Rueckwechsel (< 3 s nach der Uebernahme): das zurueckkehrende Geraet bleibt (Rueckfall {by, gen} -> stale).
//  - Begleitgeraet -> "Hierher uebernehmen" im Anruffenster (promote, Ton-Bein neu): B hoert danach das Begleitgeraet.
//  - Am Ende genau zwei Endpunkte, das alte Geraet ohne Bein.
// MESH=1: derselbe Ablauf ohne Knoten (1:1 direkt, Mesh-Rueckfall nach handover_s/2) - laeuft auch im Container.
//   SAGA_BASE=... SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... [RUNDEN=10] [MESH=1] node tests/browser_horn_pingpong.js
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE, RUNDEN = +(E.RUNDEN || 10), MESH = E.MESH === '1';
// Ziel je Uebernahme (Konzept T1): ueber Knoten 3 s; Mesh baut ICE je Paar neu auf (Container mit 2 Kernen: 5 s)
const LIM = +(E.HO_MS || (MESH ? 5000 : 3000));
if (!BASE || !E.SAGA_USER || !E.SAGA_USER2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const res = [], PERF = { ms: [] };
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function until(fn, ms = 20000, step = 150) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const sleep = ms => new Promise(r => setTimeout(r, ms));
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
const removed = h => (h && h.errs || []).some(e => /entfernt/.test(e.err || '') || e.status === 401);
// verbunden: Knoten (horn().up) bzw. Mesh (alle Verbindungen zu anderen Personen)
async function up(p) {
  if (!MESH) { const h = await horn(p); return !!(h && h.up); }
  const r = await room(p); const o = r && r.links.filter(l => !l.me);
  return !!(o && o.length && o.every(l => l.connected));
}

// B hoert das Geraet d (Pegel ueber den Knoten) innerhalb von ms
async function hears(b, d, ms = 4000) {
  if (MESH) return until(async () => { const r = await room(b); const l = r && r.links.find(x => x.device === d); return l && l.connected && l.playing && !l.muted; }, ms, 200);
  return until(async () => { const h = await horn(b); return h && h.up && (h.levels || {})[d] > 0.01; }, ms, 200);
}
// Uebernahme von "from" nach "to" aus der Leiste; -> ms bis Ton an und verbunden (oder null)
async function takeOver(to) {
  await until(() => to.evaluate(() => !!document.querySelector('.cl-run .cl-run-go')), 15000, 100);
  const t0 = Date.now();
  await to.click('.cl-run .cl-run-go');
  const r = await until(async () => { const x = await room(to); return x && !x.ho && !x.companion && x.mic && await up(to) && x; }, 15000, 100);
  return r ? Date.now() - t0 : null;
}

(async () => {
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  br = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] });
  try {
    const a1 = await session(U1, E.SAGA_PASS, 'A1'), b = await session(U2, E.SAGA_PASS2, 'B');
    await chatKeys(a1, U1); await chatKeys(b, U2);
    const a2 = await session(U1, E.SAGA_PASS, 'A2');
    await chatKeys(a2, U1);
    const D = { a1: await dev(a1), a2: await dev(a2) };
    ok('zwei Geräte von A und eins von B mit Chat-Schlüsseln', D.a1 && D.a2 && D.a1 !== D.a2 && await dev(b));
    const feat = await a1.evaluate(() => fetch('/api/calls/config', { headers: { 'X-Saga': '1' } }).then(r => r.json()));
    ok('Server meldet handover2' + (MESH ? '' : ' und horn_dm'), (feat.features || []).includes('handover2') && (MESH || feat.horn_dm), feat.features);

    await a1.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a1.waitForSelector('#ct-chat:not([hidden])'); await a1.waitForTimeout(1000);
    await b.goto(BASE + '/chats'); await a2.goto(BASE + '/chats'); await a2.waitForTimeout(1500);
    await a1.click('#ct-call-v');
    await b.waitForSelector('.cl-ring', { timeout: 20000 }); await b.click('.cl-ring .cl-big');
    const h1 = await until(async () => await up(a1) && await up(b), 30000);
    ok('1:1 A1 <-> B ' + (MESH ? 'direkt' : 'über den Knoten'), h1, [await horn(a1), await horn(b), await room(b)]);
    ok('B hört A1', await hears(b, D.a1, 8000), (await horn(b) || {}).levels);

    // --- Hin und Her ----------------------------------------------------------------------------------------------
    const P = { a1, a2 };
    let cur = 'a1', nxt = 'a2', good = 0, twoMics = 0, bans = 0;
    for (let i = 0; i < RUNDEN; i++) {
      const ms = await takeOver(P[nxt]);
      PERF.ms.push(ms);
      const gone = await until(async () => !(await room(P[cur])), 6000, 150);
      const rb = await until(async () => { const r = await room(b); return r && await up(b) && r.legs.includes(D[nxt]) && !r.legs.includes(D[cur]) && r; }, 8000, 200);
      const heard = await hears(b, D[nxt]);
      const mics = (await Promise.all([room(a1), room(a2)])).filter(x => x && x.mic && !x.companion).length;
      if (mics > 1) twoMics++;
      if (removed(await horn(P[nxt]))) bans++;
      const fine = ms !== null && ms <= LIM && gone && rb && heard;
      const old = await room(P[cur]);
      if (fine) good++; else console.log('  Runde', i, { ms, gone: !!gone, rb: !!rb, heard: !!heard, h: await horn(P[nxt]) && (await horn(P[nxt])).errs,
        old: old && { ho: old.ho, mic: old.mic, companion: old.companion, legs: old.legs.length, links: JSON.stringify(old.links.map(l => [l.device.slice(0, 4), l.me, l.connected, l.established])) } });
      [cur, nxt] = [nxt, cur];
      await sleep(i % 2 ? 1000 : 5000);
    }
    ok(`Hin und Her: ${good}/${RUNDEN} Übernahmen ≤ ${LIM / 1000} s, B hört jeweils das neue Gerät (ms ${PERF.ms.join(',')})`, good === RUNDEN);
    ok(`... nie zwei eigene Geräte mit offenem Mikrofon (${twoMics}), kein 401 "entfernt" (${bans})`, twoMics === 0 && bans === 0);

    // --- Schneller Rueckwechsel (< 3 s) -----------------------------------------------------------------------------
    const msA = await takeOver(P[nxt]);
    await sleep(800);
    const msB = await takeOver(P[cur]);                 // sofort zurueck (der Rueckfall des anderen kommt erst nach 3 s)
    await sleep(4000);
    const rr = await room(P[cur]), hr = await horn(P[cur]);
    ok(`schneller Rückwechsel: zurückgekehrtes Gerät bleibt nach dem Rückfall (${msA}/${msB} ms)`, rr && rr.mic && !rr.companion && await up(P[cur]), [rr, hr && hr.errs]);
    ok('... B hört es', await hears(b, D[cur]));

    // --- Begleitgeraet -> Hierher uebernehmen (promote, Ton-Recht) ---------------------------------------------------
    const comp = P[nxt];
    await until(() => comp.evaluate(() => !!document.querySelector('.cl-run .cl-pill')), 15000, 150);
    await comp.click('.cl-run .cl-pill:not(.cl-run-go)');   // "Hinzufügen"
    const rc = await until(async () => { const x = await room(comp); return x && x.companion && await up(comp) && x; }, 15000);
    ok('Begleitgerät verbunden (still)', rc && !rc.mic, rc);
    await until(() => comp.evaluate(() => !!document.querySelector('.cl-take')), 8000, 150);
    const t0 = Date.now();
    await comp.click('.cl-take');
    const rt = await until(async () => { const x = await room(comp); return x && !x.ho && !x.companion && x.mic && x; }, 15000, 100);
    ok(`Begleitgerät übernimmt (${Date.now() - t0} ms)`, rt, await room(comp));
    ok('... B hört das frühere Begleitgerät (Ton-Bein mit neuen Token)', await hears(b, D[nxt], 6000), (await horn(b) || {}).levels);
    ok('... das bisherige Hauptgerät hat aufgelegt', await until(async () => !(await room(P[cur])), 8000, 200));
    const fin = await until(async () => { const r = await room(b); return r && r.legs.length === 2 && r; }, 6000, 200) || await room(b);
    ok(`am Ende genau zwei Endpunkte (${fin && fin.legs.length})`, fin && fin.legs.length === 2);
    console.log('PERF ' + JSON.stringify(PERF));
    await b.evaluate(id => fetch('/api/calls/' + id + '/end', { method: 'POST', credentials: 'same-origin', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'hangup' }) }), fin && fin.id).catch(() => {});   // aufraeumen: naechster Lauf klingelt sofort
  } catch (e) {
    ok('Ablauf', false, String(e && e.stack || e));
  } finally {
    await br.close();
    const n = res.filter(Boolean).length;
    console.log(`\nHORN-PINGPONG ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
    process.exit(n === res.length ? 0 : 1);
  }
})();
