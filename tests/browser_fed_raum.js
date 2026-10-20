// Browser-Ende-zu-Ende fuer Anrufe 2.4 E4/E5 UEBER SERVERGRENZEN (static/calls.js, saga-call/v2 mit Handles) - drei
// verbundene Instanzen A (Heim), B, C (B und C nicht miteinander verbunden), je Geraet ein headless Chrome:
//   BJS=1 python3 tests/foederation_anrufe_raum.py      (startet A, B, C und dann dieses Skript mit SAGA_A/B/C, SAGA_USER_*)
// Geprueft: 1:1 Anna (A) <-> Bert (B), Annas zweites Geraet tritt ueber die Leiste als Begleitgeraet bei (Mesh zu Bert und
// zum eigenen Geraet), "Hierher übernehmen" ohne Abbruch; Gruppenanruf in einer Gruppe ueber drei Server, gestartet von Bert
// (B, der Heim-Server A fuehrt ihn): klingelt bei Anna und Dora, 4er-Mesh (Anna, Annas zweites Geraet, Bert, Dora) mit
// Verbindungen B <-> C, deren Signale ueber A laufen; alle Fingerabdruecke geprueft; Verlassen; Ende.
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, A = E.SAGA_A, B = E.SAGA_B, CC = E.SAGA_C;
if (!A || !B || !CC) { console.error('SAGA_A, SAGA_B, SAGA_C, SAGA_USER_A/B/C, SAGA_PASS setzen'); process.exit(2); }
const res = [], PERF = {}, BROWSERS = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function shot(p, name) { if (E.SHOTS) { fs.mkdirSync(E.SHOTS, { recursive: true }); await p.screenshot({ path: path.join(E.SHOTS, 'fedraum-' + name + '.png') }); } }
async function until(fn, ms = 20000, step = 150) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

async function session(base, user, name) {
  const br = await PW.chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required',
           '--unsafely-treat-insecure-origin-as-secure=' + [A, B, CC].map(u => new URL(u).origin).join(',')] });
  BROWSERS.push(br);
  const ctx = await br.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: base });
  const p = await ctx.newPage();
  p.__name = name; p.__base = base;
  p.on('pageerror', e => console.log('  [pageerror ' + name + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + name + '] ' + m.text()));
  await p.goto(base + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  return p;
}
const RKF = u => path.join(os.tmpdir(), 'saga-fedraum-rk-' + u + '.txt');
async function chatKeys(q, user) {
  await q.goto(q.__base + '/chats'); await q.waitForTimeout(2500);
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
const api = (p, m, url, body) => p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, data: e.data, msg: e.message })), [m, url, body]);
const room = p => p.evaluate(() => window.HKCalls && window.HKCalls.room());
const dev = p => p.evaluate(() => window.HKChat.state().device);
async function liveOk(...ps) { for (const p of ps) await until(() => p.evaluate(() => !!(window.HKLive && window.HKLive.state().sse)), 15000, 200); }
const allConn = (r, n) => r && r.links.length === n && r.links.every(l => l.connected && l.verified) && r;
async function chatByRef(p, ref) {
  return until(async () => { const r = await api(p, 'GET', '/api/chats?device=' + (await dev(p))); return r.ok && (r.r.chats || []).find(c => c.ref === ref); }, 20000, 400);
}

(async () => {
  try {
    const pa = await session(A, E.SAGA_USER_A, 'A'), pb = await session(B, E.SAGA_USER_B, 'B'), pc = await session(CC, E.SAGA_USER_C, 'C');
    await chatKeys(pa, 'a'); await chatKeys(pb, 'b'); await chatKeys(pc, 'c');
    const pa2 = await session(A, E.SAGA_USER_A, 'A2');
    await chatKeys(pa2, 'a');
    const me = {};
    for (const [k, p] of [['a', pa], ['a2', pa2], ['b', pb], ['c', pc]]) {
      me[k] = await until(async () => { const r = await api(p, 'GET', '/api/chat/v2/me'); return r.ok && !r.r.need && r.r.device_set_ok && r.r; }, 20000, 500);
    }
    ok('Zertifikate v2 + signierte Geraete-Listen (A mit zwei Geraeten, B, C)', me.a && me.a2 && me.b && me.c, me);
    const dA = await dev(pa), dA2 = await dev(pa2), dB = await dev(pb), dC = await dev(pc);

    // --- 1:1 A <-> B, Begleitgeraet, Uebernahme ---------------------------------------------------------------------
    const dm = await api(pa, 'POST', '/api/chats', { kind: 'dm', handle: me.b.handle, device: dA });
    ok('A: Direkt-Chat mit Bert (B)', dm.ok, dm);
    const bdm = await chatByRef(pb, dm.r.chat.ref);
    await api(pb, 'POST', '/api/chats/' + bdm.id + '/request', { action: 'accept' });
    await pa.goto(A + '/chats#c' + dm.r.chat.id); await pa.waitForSelector('#ct-chat:not([hidden])');
    await pb.goto(B + '/chats'); await pa2.goto(A + '/chats'); await liveOk(pa, pb, pa2);
    await until(() => pa.isVisible('#ct-call-a'), 8000);
    await pa.click('#ct-call-a');
    await pb.waitForSelector('.cl-ring', { timeout: 20000 }); await pb.click('.cl-ring .cl-big');
    const r1 = await until(async () => allConn(await room(pa), 1), 25000);
    ok('1:1 ueber Servergrenzen verbunden und geprueft', r1, await room(pa));
    const bar = await until(async () => pa2.evaluate(() => { const x = document.querySelector('.cl-run'); return x && x.textContent; }), 15000);
    ok('A2: Leiste "Anruf läuft auf …" (foederierter Anruf)', bar && /Hinzufügen/.test(bar), bar);
    await pa2.click('.cl-run .cl-pill:not(.cl-run-go)');
    const r2 = await until(async () => allConn(await room(pa2), 2), 25000);
    ok('A2: Begleitgeraet, verbunden mit Bert (B) und dem eigenen Geraet', r2 && r2.companion && r2.links.map(l => l.device).sort().join() === [dA, dB].sort().join(), await room(pa2));
    ok('B: zwei Verbindungen (Anna und Annas Begleitgeraet)', await until(async () => allConn(await room(pb), 2), 15000), await room(pb));
    await pa2.click('.cl-take');
    ok('A: übernommen, nicht mehr im Anruf', await until(async () => !(await room(pa)), 15000));
    ok('B: laeuft weiter mit A2', await until(async () => { const r = allConn(await room(pb), 1); return r && r.links[0].device === dA2 && r; }, 15000),
       [await room(pb), await api(pb, 'GET', '/api/calls/' + r1.id), await api(pa2, 'GET', '/api/calls/' + r1.id)]);
    await shot(pb, '1-uebernommen');
    await pb.click('.cl-ctl .cl-red');
    ok('Ende auch auf A2', await until(async () => !(await room(pa2)), 15000));

    // --- Gruppenanruf ueber drei Server ----------------------------------------------------------------------------
    const g = await api(pa, 'POST', '/api/chats', { kind: 'free', members: [], handles: [me.b.handle, me.c.handle], device: dA });
    ok('A: Gruppe ueber drei Server', g.ok && g.r.chat.v === 2, g);
    const gb = await chatByRef(pb, g.r.chat.ref), gc = await chatByRef(pc, g.r.chat.ref);
    await api(pb, 'POST', '/api/chats/' + gb.id + '/request', { action: 'accept' });
    await api(pc, 'POST', '/api/chats/' + gc.id + '/request', { action: 'accept' });
    await pa.goto(A + '/chats#c' + g.r.chat.id); await pb.goto(B + '/chats#c' + gb.id); await pc.goto(CC + '/chats#c' + gc.id); await pa2.goto(A + '/chats#c' + g.r.chat.id);
    await pb.waitForSelector('#ct-chat:not([hidden])'); await liveOk(pa, pb, pc, pa2);
    // der Kopf kann kurz mit dem Stand vor dem Laden der Chat-Daten bzw. der Geraete-Faehigkeiten gezeichnet sein: auf den
    // endgueltigen Knopf warten, sonst den letzten Stand melden
    const hd = await until(() => pb.evaluate(() => { const v = document.getElementById('ct-call-v'); return !v.hidden && v.title === 'Gruppen-Videoanruf' && v.title; }), 10000)
      || await pb.evaluate(() => { const v = document.getElementById('ct-call-v'); return v.hidden ? '(verborgen)' : v.title; });
    ok('B: Knopf "Gruppen-Videoanruf" (alle Server melden calls3)', hd === 'Gruppen-Videoanruf', hd);
    await pb.click('#ct-call-v');
    await pa.waitForSelector('.cl-ring', { timeout: 20000 }); await pc.waitForSelector('.cl-ring', { timeout: 20000 });
    ok('klingelt bei Anna (A) und Dora (C)', true);
    let t0 = Date.now();
    await pa.click('.cl-ring .cl-big');
    await until(async () => allConn(await room(pb), 1), 20000);
    await pc.click('.cl-ring .cl-big');
    const rc = await until(async () => allConn(await room(pc), 2), 20000);
    ok('C: Dora verbunden mit Anna und Bert (B <-> C ueber A signalisiert)', rc && rc.links.some(l => l.device === dB), await room(pc));
    await until(async () => pa2.evaluate(() => !!document.querySelector('.cl-run')), 15000);
    t0 = Date.now();
    await pa2.click('.cl-run .cl-pill:not(.cl-run-go)');
    const r4 = await Promise.all([pa, pa2, pb, pc].map(p => until(async () => allConn(await room(p), 3), 25000)));
    PERF.fed_mesh4_aufbau_ms = Date.now() - t0;
    ok(`4er-Mesh ueber drei Server: alle mit drei Verbindungen, geprueft (${PERF.fed_mesh4_aufbau_ms} ms)`, r4.every(Boolean), r4.map(r => r && r.links.length));
    await shot(pb, '2-mesh4');
    await pc.click('.cl-ctl .cl-red');
    ok('C geht, die anderen laufen weiter', await until(async () => allConn(await room(pb), 2), 15000), await room(pb));
    await pa2.click('.cl-ctl .cl-red');
    await pb.click('.cl-ctl .cl-red');
    ok('B geht - nur noch Anna -> Ende', await until(async () => !(await room(pa)), 15000));
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e));
    for (const br of BROWSERS) for (const ctx of br.contexts()) for (const p of ctx.pages()) {
      const t = await p.evaluate(async () => [document.querySelector('.cl-run') && document.querySelector('.cl-run').textContent,
        (await window.HK.api('GET', '/api/calls/active')).calls.map(x => [x.id.slice(0, 6), x.kind, x.state, x.joinable, x.join_block, x.mine,
          x.legs.map(l => l.device.slice(0, 6) + '/' + l.role + '/' + (l.caps || []).join('+'))])]).catch(x => String(x));
      console.log('  [' + p.__name + '] ' + JSON.stringify(t).slice(0, 1500));
    }
  } finally {
    for (const br of BROWSERS) await br.close().catch(() => {});
  }
  console.log('PERF ' + JSON.stringify(PERF));
  console.log(`\nBROWSER-FED-RAUM ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
