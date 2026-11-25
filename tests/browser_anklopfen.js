// Browser-Ende-zu-Ende fuer 2.6 (Bugs-DB 2029-5 Teil B): Anklopfen und zurueckgehaltene Anrufe im Web (static/calls.js,
// Vertrag docs/APP-API.md "Anrufe und eigener Status"). Vier Konten in einem headless Browser (Kamera/Mikrofon-Attrappe):
//   SAGA_BASE=http://host:port SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... SAGA_USER3=c SAGA_PASS3=... \
//   SAGA_USER4=d SAGA_PASS4=... [SHOTS=/pfad] [SHOT_PREFIX=nachher|vorher] [VORHER=<calls.js>] [VORHER_CSS=<calls.css>] \
//   [BR=firefox] [PLAYWRIGHT=...] [PW_CHANNEL=chromium] node tests/browser_anklopfen.js
// Testumgebung: tools/testumgebung/umgebung.sh lauf server <slot> browser_anklopfen.js (Konten um-anna/-ben/-cora/-dora).
// Geprueft: A meldet waiting1; A telefoniert mit B, C ruft A -> bei A Leiste "klopft an" im Anruffenster (kein Klingel-
// Dialog, kein Titel-Blinken, Rollen/Namen), C sieht "Klopft an …"; "Ablehnen" -> C besetzt, A und B sprechen weiter;
// C ruft noch einmal -> "du hast schon angeklopft" (again), bei A nichts; D ruft A -> "Beenden und annehmen" -> A spricht
// mit D, B ist draussen; A auf Nicht stoeren -> B ruft -> stille Leiste (call.quiet) ohne Klingeln, B sieht "Klingelt …",
// B legt auf -> Leiste weg; B ruft binnen 180 s erneut -> klingelt normal (Wiederholungsanruf); D ruft -> stille Leiste
// -> "Annehmen" verbindet. Bilder (hell/dunkel, 1440x900 und 390x844): Anklopf-Leiste, "Klopft an …" beim Anrufer,
// stille Leiste. VORHER=<Datei>: der Web-Stand vor 2029-5 (calls.js/calls.css aus main e1a874e per Route) - nur Bilder.
'use strict';
const pw = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_USER2 || !E.SAGA_USER3 || !E.SAGA_USER4) { console.error('SAGA_BASE, SAGA_USER/PASS … SAGA_USER4/PASS4 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '', PRE = E.SHOT_PREFIX || (E.VORHER ? 'vorher' : 'nachher'), OLD = !!E.VORHER, FF = E.BR === 'firefox';
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function until(fn, ms = 20000, step = 150) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function shot(p, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await p.screenshot({ path: path.join(SHOTS, PRE + '-' + name + (FF ? '-ff' : '') + '.png') });
}
let br;
async function session(user, pass, name) {
  const ctx = await br.newContext({ viewport: { width: 1440, height: 900 }, locale: 'de-DE', colorScheme: 'dark' });
  if (!FF) await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.__name = name; p.__caps = null;
  if (OLD) {                                         // Stand vor 2029-5: calls.js/calls.css aus main e1a874e
    const js = fs.readFileSync(E.VORHER, 'utf8'), css = E.VORHER_CSS ? fs.readFileSync(E.VORHER_CSS, 'utf8') : null;
    await p.route(/\/static\/calls\.js(\?.*)?$/, r => r.fulfill({ status: 200, contentType: 'application/javascript; charset=utf-8', body: js }));
    if (css) await p.route(/\/static\/calls\.css(\?.*)?$/, r => r.fulfill({ status: 200, contentType: 'text/css; charset=utf-8', body: css }));
  }
  // Faehigkeiten des Geraets (PUT /api/calls/caps, capsReport) - erst danach kann der Server bei A anklopfen
  p.on('request', r => { if (r.method() === 'PUT' && /\/api\/calls\/caps$/.test(r.url())) { try { p.__capsReq = JSON.parse(r.postData() || '{}').caps; } catch (e) { /* egal */ } } });
  p.on('response', r => { if (r.request().method() === 'PUT' && /\/api\/calls\/caps$/.test(r.url()) && r.ok()) p.__caps = p.__capsReq || []; });
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
async function api(p, m, url, body) { return p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, data: e.data, msg: e.message })), [m, url, body]); }
const st = p => p.evaluate(() => window.HKCalls && window.HKCalls.state());
const held = p => p.evaluate(() => window.HKCalls && window.HKCalls.held ? window.HKCalls.held() : []);
const ringing = p => p.evaluate(() => window.HKCalls ? window.HKCalls.ringing() : []);
// Zeile des neuesten Anruffensters (ein beendetes bleibt 3,5 s stehen)
const winStatus = p => p.evaluate(() => { const w = [...document.querySelectorAll('.cl-win')].pop(); const s = w && w.querySelector('.cl-status'); return s && !s.hidden ? s.textContent : ''; });
const connected = p => until(async () => { const s = await st(p); return s && s.connected && s; }, 30000, 250);
async function openDm(p, user) {
  await p.goto(BASE + '/chats#u' + encodeURIComponent(user));
  await p.waitForSelector('#ct-chat:not([hidden])', { timeout: 20000 });
  await until(() => p.evaluate(() => { const v = document.getElementById('ct-call-a'); return v && !v.hidden; }), 15000);
  await until(() => p.evaluate(() => !!(window.HKLive && window.HKLive.state().sse)), 15000, 200);
  await p.waitForTimeout(600);
}
async function hangup(p) { const h = await p.$('.cl-win:not(.cl-done) .cl-ctl .cl-red'); if (h) await h.click().catch(() => {}); }
async function idle(p) { await until(async () => !(await st(p)), 10000, 200); await p.waitForTimeout(300); }
async function theme(p, light) {
  await p.emulateMedia({ colorScheme: light ? 'light' : 'dark' });
  await p.evaluate(l => { document.documentElement.dataset.theme = l ? 'light' : 'dark'; }, light);
  await p.waitForTimeout(250);
}
// Bilder einer Seite: breit dunkel/hell, schmal hell/dunkel - danach wieder breit dunkel
async function shots4(p, name) {
  await theme(p, false); await p.waitForTimeout(250); await shot(p, name + '-breit-dunkel');
  await theme(p, true); await shot(p, name + '-breit-hell');
  await p.setViewportSize({ width: 390, height: 844 }); await p.waitForTimeout(600); await shot(p, name + '-schmal-hell');
  await theme(p, false); await shot(p, name + '-schmal-dunkel');
  await p.setViewportSize({ width: 1440, height: 900 }); await p.waitForTimeout(400);
}
const noBlink = p => p.evaluate(() => !/📞|ruft an/.test(document.title));

(async () => {
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2, U3 = E.SAGA_USER3, U4 = E.SAGA_USER4;
  const NB = E.SAGA_NAME2 || 'Ben Birke', NC = E.SAGA_NAME3 || 'Cora Celle', ND = E.SAGA_NAME4 || 'Dora Distel';
  br = FF ? await pw.firefox.launch({ headless: true, firefoxUserPrefs: { 'media.navigator.permission.disabled': true,
      'media.navigator.streams.fake': true, 'media.autoplay.default': 0, 'network.proxy.type': 0,
      'media.peerconnection.ice.loopback': true, 'media.peerconnection.ice.obfuscate_host_addresses': false } })
    : await pw.chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
      args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + new URL(BASE).origin,
             '--autoplay-policy=no-user-gesture-required'] });
  console.log('Browser: ' + (FF ? 'Firefox' : 'Chromium') + ' ' + br.version() + (OLD ? ' (VORHER: Stand vor 2029-5)' : ''));
  try {
    const a = await session(U1, E.SAGA_PASS, 'A'), b = await session(U2, E.SAGA_PASS2, 'B');
    const c = await session(U3, E.SAGA_PASS3, 'C'), d = await session(U4, E.SAGA_PASS4, 'D');
    for (const [p, u] of [[a, U1], [b, U2], [c, U3], [d, U4]]) await chatKeys(p, u);
    ok('vier Konten mit Chat-Schluesseln', await a.evaluate(() => !!(window.HKChat && window.HKChat.state().device)));
    const cfg = await api(a, 'GET', '/api/calls/config');
    ok('Server meldet waiting1 (GET /api/calls/config)', cfg.ok && (cfg.r.features || []).indexOf('waiting1') >= 0, cfg.r && cfg.r.features);
    await api(a, 'PUT', '/api/account/status', { state: 'available', source: 'manual' });
    // A im Direkt-Chat mit B; B, C und D im Direkt-Chat mit A
    await openDm(b, U1); await openDm(c, U1); await openDm(d, U1); await openDm(a, U2);
    const capsA = await until(() => a.__caps, 20000, 200);
    if (!OLD) ok('A meldet waiting1 (PUT /api/calls/caps), nicht mehr als 16 Faehigkeiten', capsA && capsA.indexOf('waiting1') >= 0 && capsA.length <= 16, capsA);
    else ok('vorher: A meldet kein waiting1', capsA && capsA.indexOf('waiting1') < 0, capsA);

    // --- 1. A telefoniert mit B -----------------------------------------------------------------------------------------
    await a.click('#ct-call-a');
    const r1 = await until(() => b.$('.cl-ring'), 20000, 200);
    if (!r1) { ok('B klingelt', false, { a: await winStatus(a), b: await b.evaluate(() => document.title) }); throw new Error('kein Klingeln bei B'); }
    await b.click('.cl-ring .cl-big');
    const sA = await connected(a), sB = await connected(b);
    ok('A und B verbunden', sA && sB, { sA: !!sA, sB: !!sB });
    await a.waitForTimeout(800);

    // --- 2. C ruft A an: Anklopfen ----------------------------------------------------------------------------------------
    await c.click('#ct-call-a');
    if (OLD) {
      const bs = await until(async () => { const t = await winStatus(c); return /Besetzt/.test(t) && t; }, 15000);
      ok('vorher: C ist sofort besetzt (kein Anklopfen)', bs, await winStatus(c));
      await a.waitForTimeout(800);
      await shots4(a, 'a-im-gespraech-c-ruft');
      await shots4(c, 'c-anrufer');
    } else {
      const hA = await until(async () => { const h = await held(a); return h.length && h[0]; }, 15000);
      ok('A: Leiste "klopft an" im Anruffenster', hA && hA.mode === 'wait' && hA.inCall && hA.text === NC + ' klopft an', hA);
      const bar = await a.evaluate(() => {
        const w = document.querySelector('.cl-win .cl-wait'); if (!w) return null;
        const r = w.getBoundingClientRect();
        return { role: w.getAttribute('role'), label: w.getAttribute('aria-label'), text: w.textContent, vis: r.width > 0 && r.height > 0,
                 btns: [...w.querySelectorAll('button')].map(x => x.getAttribute('aria-label') || x.textContent) };
      });
      ok('... Rolle group mit Namen, Knoepfe "Ablehnen" und "Beenden und annehmen" (sichtbar)',
         bar && bar.role === 'group' && bar.vis && bar.btns.indexOf('Ablehnen') >= 0 && bar.btns.some(x => /^Aktuelles Gespräch beenden und den Anruf von/.test(x)), bar);
      const byRole = await a.getByRole('button', { name: 'Ablehnen' }).count();
      ok('... per Rolle auffindbar (getByRole button "Ablehnen")', byRole === 1, byRole);
      await a.waitForTimeout(2500);
      ok('A: kein Klingel-Dialog, nichts klingelt, kein Titel-Blinken', !(await a.$('.cl-ring')) && !(await ringing(a)).length && await noBlink(a),
         { ring: !!(await a.$('.cl-ring')), title: await a.evaluate(() => document.title) });
      ok('A und B sprechen weiter', (await st(a) || {}).connected && (await st(b) || {}).connected);
      const kc = await until(async () => { const t = await winStatus(c); return /Klopft an/.test(t) && t; }, 8000);
      ok('C: "Klopft an …" statt "Klingelt …"', kc, await winStatus(c));
      const ann = await a.evaluate(() => (document.querySelector('.cl-win .cl-say') || {}).textContent);
      ok('A: Ansage fuer Bildschirmleser', ann === NC + ' klopft an', ann);
      await shots4(a, 'anklopf-leiste');
      await shots4(c, 'anrufer-klopft-an');
      // Tastatur: Fokus auf "Ablehnen" (Tab-Reihenfolge im Fenster), Enter
      await a.focus('.cl-wait .cl-hold-no');
      const fv = await a.evaluate(() => document.activeElement && document.activeElement.classList.contains('cl-hold-no'));
      await a.keyboard.press('Enter');
      const gone = await until(async () => !(await held(a)).length && !(await a.$('.cl-wait')), 5000);
      ok('A: "Ablehnen" per Tastatur -> Leiste weg', fv && gone, { fv, gone });
      const cb = await until(async () => { const t = await winStatus(c); return /Besetzt/.test(t) && t; }, 10000);
      ok('C: besetzt (decline busy)', cb, await winStatus(c));
      await a.waitForTimeout(1000);
      ok('A und B sprechen nach dem Ablehnen weiter', (await st(a) || {}).connected && (await st(b) || {}).connected);

      // --- 3. C ruft noch einmal: again ---------------------------------------------------------------------------------
      await idle(c);
      await c.click('#ct-call-a');
      const ag = await until(async () => { const t = await winStatus(c); return /schon angeklopft/.test(t) && t; }, 15000);
      ok('C ruft noch einmal: "… telefoniert noch – du hast schon angeklopft." (409 again)', ag && ag.indexOf('Anna') >= 0, await winStatus(c));
      await a.waitForTimeout(1500);
      ok('... bei A keine Leiste und kein Klingeln', !(await held(a)).length && !(await a.$('.cl-ring')));
      await shot(c, 'anrufer-again-breit-dunkel');

      // --- 4. D ruft A: "Beenden und annehmen" --------------------------------------------------------------------------
      await d.click('#ct-call-a');
      const hD = await until(async () => { const h = await held(a); return h.length && h[0]; }, 15000);
      ok('A: Leiste "klopft an" fuer D', hD && hD.mode === 'wait' && hD.text === ND + ' klopft an', hD);
      await a.click('.cl-wait .cl-hold-go');
      const sAD = await connected(a), sD = await connected(d);
      ok('"Beenden und annehmen": A spricht mit D (gleicher Anruf)', sAD && sD && sAD.id === sD.id, { a: sAD && sAD.id, d: sD && sD.id });
      const bEnd = await until(async () => !(await st(b)), 10000, 200);
      ok('... B ist draussen (Gespraech beendet)', bEnd);
      ok('... genau ein Anruffenster bei A', await a.evaluate(() => document.querySelectorAll('.cl-win:not(.cl-done)').length === 1));
      await hangup(a); await idle(a); await idle(d);
    }
    if (OLD) { await hangup(a); await idle(a); await idle(b); await idle(c); }
    await b.waitForTimeout(3800);                      // beendete Fenster schliessen

    // --- 5. A auf Nicht stoeren: B ruft -> stille Leiste ------------------------------------------------------------------
    const dnd = await api(a, 'PUT', '/api/account/status', { state: 'dnd', source: 'manual' });
    ok('A setzt Nicht stoeren (PUT /api/account/status)', dnd.ok && dnd.r.effective === 'dnd', dnd);
    await b.click('#ct-call-a');
    const kb = await until(async () => { const t = await winStatus(b); return /Klingelt/.test(t) && t; }, 15000);
    ok('B: "Klingelt …" (A sieht davon nur die stille Leiste)', kb && !/Klopft/.test(kb), await winStatus(b));
    if (OLD) {
      await a.waitForTimeout(2500);
      ok('vorher: bei A nichts (call.quiet unbekannt)', !(await a.$('.cl-ring')));
      await shots4(a, 'a-nicht-stoeren-b-ruft');
      await hangup(b); await idle(b);
    } else {
      const q = await until(async () => { const h = await held(a); return h.length && h[0]; }, 10000);
      ok('A: stille Leiste "Anruf von … – stumm (Nicht stören)"', q && q.mode === 'quiet' && !q.inCall && q.text === 'Anruf von ' + NB + ' – stumm (Nicht stören)', q);
      await a.waitForTimeout(2500);
      ok('A: kein Klingel-Dialog, nichts klingelt, kein Titel-Blinken', !(await a.$('.cl-ring')) && !(await ringing(a)).length && await noBlink(a),
         { ring: !!(await a.$('.cl-ring')), title: await a.evaluate(() => document.title) });
      const qb = await a.evaluate(() => { const w = document.querySelector('.cl-quiet.cl-hold-page'); return w && { label: w.getAttribute('aria-label'), btns: [...w.querySelectorAll('button')].map(x => x.getAttribute('aria-label') || x.textContent) }; });
      ok('... mit "Annehmen" und "Hinweis ausblenden" (Namen)', qb && qb.btns.some(x => /^Anruf von .* annehmen$/.test(x)) && qb.btns.indexOf('Hinweis ausblenden') >= 0, qb);
      const act = await api(a, 'GET', '/api/calls/active');
      ok('... GET /api/calls/active liefert den Anruf nicht (nichts klingelt beim Laden)', act.ok && !(act.r.calls || []).some(x => x.role === 'callee' && x.state === 'ringing'), act.r);
      await shots4(a, 'stille-leiste');
      await hangup(b);
      ok('B legt auf -> stille Leiste weg', await until(async () => !(await held(a)).length && !(await a.$('.cl-quiet')), 8000));
      await idle(b);

      // --- 6. B ruft binnen 180 s erneut: klingelt normal -----------------------------------------------------------------
      await b.waitForTimeout(3800);
      await b.click('#ct-call-a');
      const rr = await until(() => a.$('.cl-ring'), 15000);
      ok('Wiederholungsanruf binnen 180 s: klingelt bei A normal (Dialog)', rr && !(await held(a)).length && (await ringing(a)).length === 1);
      await a.click('.cl-ring .cl-red');
      await idle(b);

      // --- 7. D ruft (Nicht stoeren) -> stille Leiste -> "Annehmen" ------------------------------------------------------
      await d.waitForTimeout(3800);
      await d.click('#ct-call-a');
      const q2 = await until(async () => { const h = await held(a); return h.length && h[0]; }, 15000);
      ok('D ruft: stille Leiste bei A', q2 && q2.mode === 'quiet' && q2.text === 'Anruf von ' + ND + ' – stumm (Nicht stören)', q2);
      await a.click('.cl-quiet .cl-hold-go');
      const sA2 = await connected(a), sD2 = await connected(d);
      ok('"Annehmen" aus der stillen Leiste verbindet A und D', sA2 && sD2 && sA2.id === sD2.id, { a: !!sA2, d: !!sD2 });
      ok('... Leiste weg', !(await held(a)).length && !(await a.$('.cl-quiet')));
      await hangup(a); await idle(a); await idle(d);
    }
    await api(a, 'PUT', '/api/account/status', { state: 'available', source: 'manual' });
  } catch (e) {
    console.log('FEHLER ' + (e && e.stack || e));
    res.push(false);
  } finally {
    await br.close().catch(() => {});
  }
  console.log(`\nBROWSER-ANKLOPFEN ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) && res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) && res.length ? 0 : 1);
})();
