// Browser-Ende-zu-Ende: "Hierher übernehmen" im DIREKTEN (Mesh-)1:1 ueber Servergrenzen (Feldfall 17.11.: iPhone -> Browser,
// Gegenueber auf einem anderen Server; die Uebernahme scheiterte nach 15 s mit "Übernahme hat nicht geklappt").
//   BJS=1 python3 tests/foederation_uebergabe_p2p.py   (startet A und B und dann dieses Skript mit SAGA_A/B, SAGA_USER_*)
// Ablauf wie im Feld: Anna ruft mit Geraet 1 (A1) Bert (B) an, Bert nimmt an (direkt, kein Knoten), Annas zweites Geraet
// (A2) holt den Anruf ueber die Leiste "Hierher übernehmen" (Rolle handover, ohne Begleitgeraet-Umweg).
// Erwartet: A2 verbunden mit Bert, A1 nicht mehr im Anruf, Bert laeuft mit A2 weiter - und keine Fehlmeldung.
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, A = E.SAGA_A, B = E.SAGA_B;
if (!A || !B) { console.error('SAGA_A, SAGA_B, SAGA_USER_A/B, SAGA_PASS setzen'); process.exit(2); }
const res = [], BROWSERS = [], TOASTS = {};
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 2500))); }
async function until(fn, ms = 20000, step = 150) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

async function session(base, user, name) {
  const br = await PW.chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required',
           '--unsafely-treat-insecure-origin-as-secure=' + [A, B].map(u => new URL(u).origin).join(',')] });
  BROWSERS.push(br);
  const ctx = await br.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: base });
  const p = await ctx.newPage();
  p.__name = name; p.__base = base; TOASTS[name] = [];
  p.on('pageerror', e => console.log('  [pageerror ' + name + '] ' + e.message));
  p.on('console', m => { if (E.DEBUG) console.log('  [console ' + name + '] ' + m.text()); });
  await p.exposeFunction('__toast', t => TOASTS[name].push(t));
  await p.addInitScript(() => {
    new MutationObserver(ms => ms.forEach(m => { const t = m.target; if (t && t.nodeType === 1 && t.className === 'toast' && t.textContent) window.__toast(t.textContent); }))
      .observe(document, { childList: true, subtree: true });
  });
  await p.goto(base + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  return p;
}
const RKF = u => path.join(os.tmpdir(), 'saga-uep2p-rk-' + u + '.txt');
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
const brief = r => r && { id: (r.id || '').slice(0, 8), ho: r.ho, companion: r.companion, topo: r.topo, legs: r.legs.map(d => d.slice(0, 6)),
  links: r.links.map(l => ({ d: (l.device || '').slice(0, 6), me: l.me, est: l.established, conn: l.connected, ver: l.verified, role: l.role, pc: l.pc })) };

(async () => {
  try {
    const pa = await session(A, E.SAGA_USER_A, 'A1'), pb = await session(B, E.SAGA_USER_B, 'B');
    await chatKeys(pa, 'a'); await chatKeys(pb, 'b');
    const pa2 = await session(A, E.SAGA_USER_A, 'A2');
    await chatKeys(pa2, 'a');
    const me = {};
    for (const [k, p] of [['a', pa], ['a2', pa2], ['b', pb]]) {
      me[k] = await until(async () => { const r = await api(p, 'GET', '/api/chat/v2/me'); return r.ok && !r.r.need && r.r.device_set_ok && r.r; }, 20000, 500);
    }
    ok('Zertifikate v2 + signierte Geraete-Listen (A mit zwei Geraeten, B)', me.a && me.a2 && me.b);
    const dA = await dev(pa), dA2 = await dev(pa2), dB = await dev(pb);
    const dm = await api(pa, 'POST', '/api/chats', { kind: 'dm', handle: me.b.handle, device: dA });
    ok('A: Direkt-Chat mit Bert (B)', dm.ok, dm);
    const bdm = await chatByRef(pb, dm.r.chat.ref);
    await api(pb, 'POST', '/api/chats/' + bdm.id + '/request', { action: 'accept' });
    await pa.goto(A + '/chats#c' + dm.r.chat.id); await pa.waitForSelector('#ct-chat:not([hidden])');
    await pb.goto(B + '/chats'); await pa2.goto(A + '/chats'); await liveOk(pa, pb, pa2);
    await until(() => pa.isVisible('#ct-call-v'), 8000);
    await pa.click('#ct-call-v');                      // Videoanruf wie im Feld
    await pb.waitForSelector('.cl-ring', { timeout: 20000 }); await pb.click('.cl-ring .cl-big');
    const r1 = await until(async () => allConn(await room(pa), 1), 25000);
    ok('1:1 ueber Servergrenzen verbunden (direkt, ohne Knoten)', r1 && !r1.horn, brief(await room(pa)));
    const bar = await until(async () => pa2.evaluate(() => { const x = document.querySelector('.cl-run'); return x && x.querySelector('.cl-run-go') && x.textContent; }), 15000);
    ok('A2: Leiste mit "Hierher übernehmen"', bar && /Hierher/.test(bar), bar);
    const t0 = Date.now();
    await pa2.click('.cl-run .cl-run-go');
    const r2 = await until(async () => { const r = await room(pa2); return r && !r.ho && r.links.some(l => l.device === dB && l.connected) && r; }, 20000, 200);
    ok(`A2: uebernommen, verbunden mit Bert (${Date.now() - t0} ms)`, r2, [brief(await room(pa2)), TOASTS.A2]);
    ok('A1: nicht mehr im Anruf', await until(async () => !(await room(pa)), 15000), brief(await room(pa)));
    ok('B: laeuft weiter mit A2', await until(async () => { const r = allConn(await room(pb), 1); return r && r.links[0].device === dA2 && r; }, 15000),
       [brief(await room(pb)), dA2.slice(0, 6)]);
    const bad = TOASTS.A2.filter(t => /nicht geklappt/.test(t));
    ok('A2: keine Meldung "Übernahme hat nicht geklappt"', !bad.length, TOASTS.A2);
    await pb.click('.cl-ctl .cl-red').catch(() => {});
    ok('Ende auch auf A2', await until(async () => !(await room(pa2)), 15000));

    // --- Gegenprobe wie im Feld: die Verbindung zum Gegenueber kommt nicht zustande (B und A2 wenden keine Kandidaten an)
    // -> nach handover_s "Übernahme hat nicht geklappt", A1 laeuft weiter; die Diagnose von A2 sagt, woran es hing
    await until(async () => !(await room(pa)) && !(await room(pb)), 10000);
    await pa.goto(A + '/chats#c' + dm.r.chat.id); await pa.waitForSelector('#ct-chat:not([hidden])');
    await pb.goto(B + '/chats'); await pa2.goto(A + '/chats'); await liveOk(pa, pb, pa2);
    const diag = [];
    pa2.on('request', r => { if (r.url().endsWith('/api/calls/diag') && r.method() === 'POST') diag.push(r.postData() || ''); });
    await pa.waitForTimeout(61000);                   // Grenze neue Anrufe je Konto und Minute (Knoten-Versuch + direkt zaehlen)
    await until(() => pa.isVisible('#ct-call-v'), 8000);
    await pa.click('#ct-call-v');
    if (!(await pb.waitForSelector('.cl-ring', { timeout: 20000 }).catch(() => null))) {
      console.log('  [kein Klingeln bei B] ' + JSON.stringify([brief(await room(pa)), TOASTS.A1.slice(-3), await api(pb, 'GET', '/api/calls/active')]).slice(0, 2500));
      throw new Error('Gegenprobe: kein Klingeln bei B');
    }
    await pb.click('.cl-ring .cl-big');
    ok('Gegenprobe: 1:1 wieder verbunden', await until(async () => allConn(await room(pa), 1), 25000), brief(await room(pa)));
    for (const p of [pb, pa2]) await p.evaluate(() => { RTCPeerConnection.prototype.addIceCandidate = function () { return Promise.resolve(); }; });
    await until(async () => pa2.evaluate(() => !!document.querySelector('.cl-run .cl-run-go')), 15000);
    TOASTS.A2.length = 0;
    await pa2.click('.cl-run .cl-run-go');
    const tf = Date.now();
    await until(async () => { const r = await room(pa2); return r && r.ho; }, 8000, 100);
    const failed = await until(async () => !(await room(pa2)), 25000, 300);
    ok(`Gegenprobe: Uebernahme nach der Frist abgebrochen (${Math.round((Date.now() - tf) / 1000)} s)`, failed && Date.now() - tf > 12000, brief(await room(pa2)));
    ok('Gegenprobe: A1 laeuft weiter mit Bert', allConn(await room(pa), 1) && !(await room(pa2)), [brief(await room(pa)), brief(await room(pa2))]);
    const ev = await until(() => { for (const d of diag) { try { const e = JSON.parse(d).events || []; const x = e.filter(y => y.k === 'ho-fail'); if (x.length) return x; } catch (e) { /* */ } } return null; }, 8000, 300);
    const toB = (ev || []).find(x => x.d.startsWith(dB.slice(0, 6))), toA1 = (ev || []).find(x => x.d.startsWith(dA.slice(0, 6)));
    console.log('  [ho-fail] ' + JSON.stringify(ev));
    ok('Diagnose (calldiag) von A2: ho-fail je Verbindung - zu Bert nicht verbunden, Antwort gesetzt, Kandidaten gezaehlt',
       toB && / est1 con0 /.test(toB.d) && / rd1 /.test(toB.d) && / l=h/.test(toB.d) && / r=h/.test(toB.d), [ev, diag.length]);
    ok('Diagnose: Verbindung zum eigenen alten Geraet verbunden (eigen, con1)', toA1 && / eigen est1 con1 /.test(toA1.d), ev);
    ok('Diagnose ohne Adressen (keine IP, kein .local-Name)', ev && !ev.some(x => /\d+\.\d+\.\d+\.\d+|\.local/.test(x.d)), ev);
    await pb.click('.cl-ctl .cl-red').catch(() => {});
    ok('Gegenprobe: Ende', await until(async () => !(await room(pa)), 15000));
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e));
  } finally {
    for (const k of Object.keys(TOASTS)) if (TOASTS[k].length) console.log('  [toasts ' + k + '] ' + JSON.stringify(TOASTS[k]).slice(0, 1500));
    for (const br of BROWSERS) await br.close().catch(() => {});
  }
  console.log(`\nBROWSER-FED-UEBERGABE-P2P ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
