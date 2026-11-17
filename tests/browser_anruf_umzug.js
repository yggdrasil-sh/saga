// Browser-Ende-zu-Ende fuer 2.5 L9 "1:1 -> Konferenz" (static/calls.js moveIn, saga/call_move.py) UEBER EINEN KNOTEN
// (calls.horn + Grundsatz Y, Knoten bereit) - drei Playwright-Browser (headless Chromium) mit Mikrofon-Attrappe aus einer
// Ton-Datei (nicht stationaerer Gleitton, damit Luecken messbar sind):
//   SAGA_BASE=... SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... SAGA_USER3=c SAGA_PASS3=... SAGA_NAME3='Cora …' \
//   TON=/pfad/ton.wav [RUNDEN=3] [GAP_MS=300] [SHOTS=/pfad] [PLAYWRIGHT=...] [PW_CHANNEL=chromium] node tests/browser_anruf_umzug.js
// Laeuft nur mit echtem Knoten (Labor bzw. Server mit eingebautem Knoten, selbst signiert: --ignore-certificate-errors).
// Je Runde: A ruft B im Direkt-Chat an (1:1 ueber den Knoten), B nimmt an, beide hoeren sich. Dann holt B (Runde 1) bzw. A
// (weitere Runden) in der Teilnehmerliste ueber "Person hinzufuegen" C dazu (Hinweis "neue Gruppe"). Geprueft: beide
// bleiben verbunden (keine Neuverbindung zum Knoten, Dauer laeuft weiter), Anruffenster wird Gruppenanruf im neuen Chat
// (A: Chat-Ansicht springt auf den neuen Chat), Toast; Tonluecke beim Umzug in beide Richtungen (laengste Stille im
// dekodierten Ton der Gegenseite, Fenster 0,5 s vor dem Klick bis 2 s nach dem Umzug) < GAP_MS, Vergleich ohne Umzug;
// C klingelt, nimmt an, ist verbunden, hoert A und B, A ratscht den Frame-Schluessel beim Beitritt (key_n steigt - C kann
// Frueheres nicht lesen). Nach dem Auflegen: im 1:1 der Eintrag "In Gruppenanruf verschoben" mit "Zur Gruppe".
// Gegenprobe der Messung: A schaltet vor dem Umzug 0,7 s stumm - die Messung bei B muss diese Stille sehen (>= 400 ms).
// Bilder (SHOTS): vorher (1:1 mit Teilnehmerliste), Feld mit Hinweis, nachher (Gruppe mit drei), Eintrag im 1:1.
// VORHER=1 (Stand ohne L9): nur das Bild der Teilnehmerliste im 1:1 (kein "Person hinzufuegen"), dann Ende.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE, RUNDEN = +(E.RUNDEN || 3), GAP = +(E.GAP_MS || 300), N3 = E.SAGA_NAME3 || 'Cora Neu';
if (!BASE || !E.SAGA_USER || !E.SAGA_USER2 || !E.SAGA_USER3) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2, SAGA_USER3/PASS3 setzen'); process.exit(2); }
const res = [], PERF = { gapB: [], gapA: [], base: [], probe: [], moveMs: [] }, SHOTS = E.SHOTS || '', OLD = E.VORHER === '1';
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function until(fn, ms = 20000, step = 150) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, name + '.png') }); } }
const BR = [];
async function session(user, pass, name) {
  const origin = new URL(BASE).origin;
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin,
           '--autoplay-policy=no-user-gesture-required', '--ignore-certificate-errors'].concat(E.TON ? ['--use-file-for-fake-audio-capture=' + E.TON] : []) });
  BR.push(br);
  const ctx = await br.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: 'dark', ignoreHTTPSErrors: true });
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
async function chatKeys(q, user, url) {
  await q.goto(url || (BASE + '/chats')); await q.waitForTimeout(2500);
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
const up = async p => { const h = await horn(p); return !!(h && h.up); };
// hoert p das Geraet d? Pegel des Knotens bzw. dekodierter Pegel seines Tonplatzes (wie browser_horn_pingpong.js)
async function hears(p, d, ms = 6000) {
  return until(async () => { const h = await horn(p); return h && h.up && ((h.levels || {})[d] > 0.01 || await heardRaw(p, h, d)); }, ms, 250);
}
async function heardRaw(b, h, d) {
  const slots = Object.values(h.legs || {}).flatMap(l => (l.slots || []).filter(x => x.class === 'audio'));
  const i = slots.findIndex(x => x.csrc && (h.csrc || {})[x.csrc >>> 0] === d);
  if (i < 0) return false;
  return b.evaluate(async (i) => {
    const a = [...document.querySelectorAll('audio.cl-raudio')].find(x => x.dataset.slot === String(i) && x.srcObject);
    const tr = a && a.srcObject.getAudioTracks()[0];
    if (!tr || tr.readyState !== 'live') return false;
    const ac = new AudioContext(), an = ac.createAnalyser(); an.fftSize = 1024;
    ac.createMediaStreamSource(new MediaStream([tr])).connect(an);
    const buf = new Float32Array(1024); let m = 0;
    for (let k = 0; k < 12; k++) { await new Promise(r => setTimeout(r, 25)); an.getFloatTimeDomainData(buf); for (const x of buf) m = Math.max(m, Math.abs(x)); }
    ac.close();
    return m > 0.01;
  }, i);
}
// Tonmessung: alle Wiedergabe-Elemente der Gegenseite (Tonplaetze des Knotens) in einen Analysator, alle 10 ms Spitzenwert
const gapOn = p => p.evaluate(() => {
  const ac = new AudioContext(), an = ac.createAnalyser(); an.fftSize = 512;
  const seen = new Set(), buf = new Float32Array(512), W = window.__gap = { t: [], v: [], stop: false, ac };
  const attach = () => document.querySelectorAll('audio.cl-raudio').forEach(a => {
    const s = a.srcObject; if (!s) return;
    s.getAudioTracks().forEach(tr => { if (tr.readyState === 'live' && !seen.has(tr.id)) { seen.add(tr.id); ac.createMediaStreamSource(new MediaStream([tr])).connect(an); } });
  });
  const tick = () => {
    if (W.stop) return;
    attach(); an.getFloatTimeDomainData(buf); let m = 0; for (const x of buf) m = Math.max(m, Math.abs(x));
    W.t.push(Date.now()); W.v.push(m); setTimeout(tick, 10);
  };
  tick(); return true;
});
const gapOff = p => p.evaluate(() => { const W = window.__gap; if (!W) return null; W.stop = true; W.ac.close(); return { t: W.t, v: W.v }; });
// laengste Stille (< 0,01) im Fenster [t0, t1] in ms; dazu Abtast-Abstand (Last im Container)
function longest(g, t0, t1) {
  let best = 0, start = null, last = null, step = 0, n = 0;
  for (let i = 0; i < g.t.length; i++) {
    const t = g.t[i]; if (t < t0 || t > t1) continue;
    if (last !== null) { step = Math.max(step, t - last); n++; }
    last = t;
    if (g.v[i] < 0.01) { if (start === null) start = t; best = Math.max(best, t - start); } else start = null;
  }
  return { gap: best, step, n };
}
const openList = async p => {
  if (!(await p.$('.cl-plist:not(.cl-addp)'))) await p.click('.cl-people');
  return !!(await until(() => p.$('.cl-plist:not(.cl-addp) .cl-add-go'), 6000));
};

(async () => {
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2, U3 = E.SAGA_USER3;
  try {
    const a = await session(U1, E.SAGA_PASS, 'A'), b = await session(U2, E.SAGA_PASS2, 'B'), c = await session(U3, E.SAGA_PASS3, 'C');
    await chatKeys(a, U1); await chatKeys(b, U2); await chatKeys(c, U3);
    const D = { a: await dev(a), b: await dev(b), c: await dev(c) };
    ok('drei Personen mit Chat-Schlüsseln', D.a && D.b && D.c);
    const cf = await a.evaluate(() => fetch('/api/calls/config', { headers: { 'X-Saga': '1' } }).then(r => r.json()));
    ok('Server meldet ' + (OLD ? 'horn_dm (Stand ohne L9)' : 'call_move und horn_dm'), (OLD || (cf.features || []).includes('call_move')) && cf.horn_dm, cf.features);
    for (let i = 0; i < RUNDEN; i++) {
      console.log('--- Runde ' + (i + 1));
      await chatKeys(a, U1, BASE + '/chats?r=' + i + '#u' + encodeURIComponent(U2));
      await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(800);
      const dmId = await a.evaluate(() => window.HKChat.state().cur);
      if (i) { await chatKeys(b, U2, BASE + '/chats?r=' + i); await chatKeys(c, U3, BASE + '/chats?r=' + i); }
      await a.click('#ct-call-a');
      await b.waitForSelector('.cl-ring', { timeout: 20000 }); await b.click('.cl-ring .cl-big');
      const h1 = await until(async () => await up(a) && await up(b), 30000);
      const rA0 = await room(a);
      ok(`R${i + 1}: 1:1 A <-> B über den Knoten (dm, Direkt-Chat)`, h1 && rA0 && (OLD || (rA0.dm && rA0.chat_id === dmId)), [rA0 && rA0.dm, rA0 && rA0.chat_id, dmId]);
      ok(`R${i + 1}: B hört A, A hört B`, await hears(b, D.a, 10000) && await hears(a, D.b, 6000), [(await horn(b) || {}).levels]);
      const adder = i === 0 ? b : a, other = i === 0 ? a : b;
      if (OLD) {
        if (!(await b.$('.cl-plist:not(.cl-addp)'))) await b.click('.cl-people');
        await until(() => b.$('.cl-plist:not(.cl-addp)'), 5000); await sleep(800);
        ok('vorher: Teilnehmerliste im 1:1 ohne „Person hinzufügen“', !(await b.$('.cl-add-go')));
        await shot(b, 'vorher-alt-1zu1-liste-b');
        break;
      }
      ok(`R${i + 1}: Teilnehmerliste mit „Person hinzufügen“ (${i === 0 ? 'angerufene Person' : 'anrufende Person'})`, await openList(adder));
      if (i === 0) { await sleep(800); await shot(adder, 'nachher-1zu1-liste-b'); }
      await adder.click('.cl-plist .cl-add-go');
      const hint = await until(() => adder.evaluate(() => { const h = document.querySelector('.cl-add-hint'); return h && h.textContent; }), 4000);
      ok(`R${i + 1}: Feld mit Hinweis „neue Gruppe“ (${hint})`, /neue Gruppe/.test(hint || ''));
      await adder.fill('.cl-addp .cl-add-q', N3.split(' ')[0]);
      const found = await until(() => adder.evaluate(nm => [...document.querySelectorAll('.cl-addp .cl-add-p')].some(x => x.querySelector('b').textContent === nm), N3), 8000);
      ok(`R${i + 1}: ${N3} in der Suche`, found);
      if (i === 0) await shot(adder, 'umzug-feld-b');
      const k0 = (await horn(a)).key_n, s0 = (await room(a)).since, hpA = JSON.stringify((await horn(a)).hp), connsA = (await horn(a)).conns.length;
      await gapOn(a); await gapOn(b);
      await sleep(1200);
      const tm0 = Date.now();                             // Gegenprobe: A 0,7 s stumm - B muss die Stille messen
      await a.evaluate(() => { const x = [...document.querySelectorAll('.cl-ctl button')].find(y => y.getAttribute('aria-label') === 'Stummschalten'); if (x) x.click(); });
      await sleep(700);
      await a.evaluate(() => { const x = [...document.querySelectorAll('.cl-ctl button')].find(y => y.getAttribute('aria-label') === 'Mikrofon einschalten'); if (x) x.click(); });
      const tm1 = Date.now();
      await sleep(2500);                                  // Vergleichsfenster ohne Umzug
      const tClick = Date.now();
      await adder.evaluate(nm => { const x = [...document.querySelectorAll('.cl-addp .cl-add-p')].find(y => y.querySelector('b').textContent === nm); if (x) x.click(); }, N3);
      const mv = await until(async () => { const ra = await room(a), rb = await room(b); return ra && rb && !ra.dm && !rb.dm && ra.chat_id !== dmId && ra.chat_id === rb.chat_id && ra; }, 10000, 50);
      const tMoved = Date.now();
      await sleep(2200);
      const gA = await gapOff(a), gB = await gapOff(b);
      const wB = longest(gB, tClick - 500, tMoved + 2000), wA = longest(gA, tClick - 500, tMoved + 2000);
      const base = Math.max(longest(gB, tm1 + 800, tClick - 100).gap, longest(gA, tm1 + 800, tClick - 100).gap);
      const probe = longest(gB, tm0 - 300, tm1 + 800).gap;
      PERF.gapB.push(wB.gap); PERF.gapA.push(wA.gap); PERF.base.push(base); PERF.probe.push(probe); PERF.moveMs.push(tMoved - tClick);
      ok(`R${i + 1}: Gegenprobe der Messung: 0,7 s stumm bei A -> B misst ${probe} ms Stille`, probe >= 400);
      ok(`R${i + 1}: Umzug in ${tMoved - tClick} ms: beide im Gruppenanruf im neuen Chat`, mv && mv.kind === 'group', await room(a));
      ok(`R${i + 1}: Tonlücke beim Umzug B←A ${wB.gap} ms, A←B ${wA.gap} ms (< ${GAP}; ohne Umzug ${base} ms; Abtastung ≤ ${Math.max(wA.step, wB.step)} ms, n ${wA.n}/${wB.n})`,
         wB.gap < GAP && wA.gap < GAP && wA.n > 50 && wB.n > 50);
      const hA1 = await horn(a), rA1 = await room(a);
      ok(`R${i + 1}: keine Neuverbindung zum Knoten (gleiche Kennungen, eine Verbindung, verbunden), Dauer läuft weiter`,
         hA1.up && JSON.stringify(hA1.hp) === hpA && hA1.conns.length === connsA && rA1.since === s0 && rA1.legs.length === 2, [hA1.hp, hpA, rA1.since, s0]);
      const cur = await until(() => a.evaluate(id => { const s = window.HKChat.state(); return s.cur === id && s.cur; }, mv.chat_id), 5000);
      ok(`R${i + 1}: A: Chat-Ansicht springt auf den neuen Chat (${cur})`, cur === mv.chat_id, [await a.evaluate(() => window.HKChat.state()), dmId, mv.chat_id, await a.evaluate(() => location.href)]);
      const toast = await until(() => other.evaluate(() => [...document.querySelectorAll('.hk-toast, .toast, [role=status]')].map(x => x.textContent).join(' | ')), 3000);
      ok(`R${i + 1}: Hinweis „Neue Gruppe – der Anruf läuft dort … weiter“ (${(toast || '').slice(0, 120)})`, /Neue Gruppe/.test(toast || ''));
      // C klingelt und tritt bei - Schluesselwechsel (Ratsche) beim Beitritt
      await c.waitForSelector('.cl-ring', { timeout: 20000 });
      await c.click('.cl-ring .cl-big');
      const hc = await until(async () => await up(c), 30000);
      ok(`R${i + 1}: C nimmt an und ist über den Knoten verbunden`, hc);
      const k1 = await until(async () => { const h = await horn(a); return h.key_n > k0 && h.known.includes(D.c) && h.key_n; }, 8000);
      ok(`R${i + 1}: A ratscht beim Beitritt von C den Frame-Schlüssel (key_n ${k0} → ${k1})`, k1 > k0);
      ok(`R${i + 1}: C hört A und B`, await hears(c, D.a, 10000) && await hears(c, D.b, 8000), (await horn(c) || {}).levels);
      ok(`R${i + 1}: A und B weiter verbunden, drei Personen`, await up(a) && await up(b) && (await room(a)).legs.length === 3);
      if (i === 0) { await openList(a); await sleep(600); await shot(a, 'nachher-gruppe-a'); }
      const id = (await room(a)).id;
      for (const p of [c, b, a]) await p.evaluate(cid => fetch('/api/calls/' + cid + '/end', { method: 'POST', credentials: 'same-origin',
        headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({ reason: 'hangup', device: window.HKChat.state().device }) }), id).catch(() => {});
      await until(async () => !(await room(a)) || (await room(a)).id !== id, 8000);
      if (i === 0) {                                      // Eintrag im 1:1
        await chatKeys(a, U1, BASE + '/chats?e=1#u' + encodeURIComponent(U2));
        await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);
        const row = await until(() => a.evaluate(() => { const r = [...document.querySelectorAll('#ct-msgs .ct-call')].map(x => x.textContent); return r.find(t => /In Gruppenanruf verschoben/.test(t)); }), 6000);
        ok(`Eintrag im 1:1: „${row}“ mit „Zur Gruppe“`, row && /Zur Gruppe/.test(row));
        await shot(a, 'nachher-eintrag-1zu1-a');
      }
      await sleep(3000);
    }
    const med = x => [...x].sort((p, q) => p - q)[Math.floor(x.length / 2)];
    console.log('PERF ' + JSON.stringify(PERF));
    if (!OLD) ok(`Tonlücke über ${RUNDEN} Umzüge: B←A Median ${med(PERF.gapB)} ms / max ${Math.max(...PERF.gapB)}, A←B Median ${med(PERF.gapA)} / max ${Math.max(...PERF.gapA)} (ohne Umzug max ${Math.max(...PERF.base)} ms)`,
       Math.max(...PERF.gapB, ...PERF.gapA) < GAP);
  } catch (e) {
    ok('Ablauf', false, String(e && e.stack || e));
  } finally {
    for (const br of BR) await br.close().catch(() => {});
    const n = res.filter(Boolean).length;
    console.log(`\nANRUF-UMZUG ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
    process.exit(n === res.length ? 0 : 1);
  }
})();
