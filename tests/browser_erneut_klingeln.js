// Browser-Ende-zu-Ende fuer 2.5 L7 "Erneut klingeln" (static/calls.js, Teilnehmerliste "Nicht dabei") - drei Playwright-
// Browser im headless Chromium mit Kamera/Mikrofon-Attrappe:
//   SAGA_BASE=http://host:port SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... SAGA_USER3=c SAGA_PASS3=... \
//   [SHOTS=/pfad] [SHOT_PREFIX=nachher|vorher] [VORHER=1] [PLAYWRIGHT=...] [PW_CHANNEL=chromium] node tests/browser_erneut_klingeln.js
// Server: Klingeldauer (calls.ring_s) am besten 10-15 s, damit "nicht angenommen" im Test erreicht wird.
// Geprueft: Gruppenanruf A, B, C - A und B im Anruf, C nicht: bei A in der Teilnehmerliste Abschnitt "Nicht dabei" mit C
// ("klingelt …", ohne Knopf), nach der Klingeldauer "nicht angenommen" ohne Neuladen (Uhr im Client), Knopf "Erneut
// klingeln" mit aria-label (Name), per Tastatur ausgeloest: sofort "klingelt …" (vorlaeufig), C klingelt wieder (Leiste),
// C lehnt ab -> bei A "hat abgelehnt" (Ereignis call.invites), Fehler 429 (nachgestellt): Zustand zurueck + verstaendlicher
// Hinweis, Fokus bleibt auf dem Knopf. Bilder: Teilnehmerliste Desktop dunkel/hell und schmal (390 px) hell.
// VORHER=1: nur die Bilder (Stand ohne L7 - kein Abschnitt "Nicht dabei").
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_USER2 || !E.SAGA_USER3) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2, SAGA_USER3/PASS3 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '', PRE = E.SHOT_PREFIX || 'nachher', OLD = !!E.VORHER;
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, PRE + '-' + name + '.png') }); } }
const BROWSERS = [];
async function session(user, pass, name) {
  const origin = new URL(BASE).origin;
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin,
           '--autoplay-policy=no-user-gesture-required'] });
  BROWSERS.push(br);
  const ctx = await br.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: 'dark' });
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
async function api(p, m, url, body) { return p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, data: e.data, msg: e.message })), [m, url, body]); }
// Zeile von C im Abschnitt "Nicht dabei": {state, button, aria, disabled} bzw. null
const rowOf = (p, name) => p.evaluate(nm => {
  const li = [...document.querySelectorAll('.cl-away .cl-pl-away')].find(x => (x.querySelector('.cl-pl-t b') || {}).textContent === nm);
  if (!li) return null;
  const b = li.querySelector('.cl-pl-ring');
  return { state: li.querySelector('.cl-away-st').textContent, button: b ? b.textContent : null, aria: b ? b.getAttribute('aria-label') : null,
           disabled: b ? b.disabled : null, head: (document.querySelector('.cl-away-h b') || {}).textContent };
}, name);
// Teilnehmerliste oeffnen (schmal: ueber "Mehr")
async function openList(p) {
  if (await p.$('.cl-plist')) return true;
  if (await p.$('.cl-ctl .cl-people')) await p.click('.cl-ctl .cl-people');
  else if (await p.$('.cl-ctl .cl-more')) { await p.click('.cl-ctl .cl-more'); await p.waitForSelector('.cl-moremenu .cl-people', { timeout: 3000 }); await p.click('.cl-moremenu .cl-people'); }
  return !!(await until(() => p.$('.cl-plist'), 4000));
}
async function theme(p, light) {
  await p.emulateMedia({ colorScheme: light ? 'light' : 'dark' });
  await p.evaluate(l => { document.documentElement.dataset.theme = l ? 'light' : 'dark'; }, light);
  await p.waitForTimeout(300);
}
async function shots(a, tag) {
  await theme(a, false); await openList(a); await a.waitForTimeout(400); await shot(a, 'desktop-dunkel' + tag);
  await theme(a, true); await a.waitForTimeout(300); await shot(a, 'desktop-hell' + tag);
  await a.setViewportSize({ width: 390, height: 844 }); await a.waitForTimeout(700); await openList(a); await a.waitForTimeout(500);
  await shot(a, 'schmal-hell' + tag);
  await a.setViewportSize({ width: 1280, height: 800 }); await theme(a, false); await a.waitForTimeout(600); await openList(a);
}

(async () => {
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2, U3 = E.SAGA_USER3;
  const NC = E.SAGA_NAME3 || 'Cora Beispiel';
  try {
    const a = await session(U1, E.SAGA_PASS, 'A'), b = await session(U2, E.SAGA_PASS2, 'B'), c = await session(U3, E.SAGA_PASS3, 'C');
    await chatKeys(a, U1); await chatKeys(b, U2); await chatKeys(c, U3);
    const dA = await dev(a);
    ok('drei Konten mit Chat-Schluesseln', dA && await dev(b) && await dev(c));
    const mk = await api(a, 'POST', '/api/chats', { kind: 'free', members: [U2, U3], device: dA });
    ok('freie Gruppe A, B, C angelegt', mk.ok, mk);
    const gid = mk.r.chat.id;
    for (const p of [a, b, c]) await p.goto(BASE + '/chats#c' + gid);
    await a.waitForSelector('#ct-chat:not([hidden])'); await liveOk(a, b, c);
    await until(() => a.evaluate(() => { const v = document.getElementById('ct-call-a'); return v && !v.hidden && /Gruppen/.test(v.title) && v; }), 10000);
    await a.click('#ct-call-a');
    await b.waitForSelector('.cl-ring', { timeout: 15000 }); await c.waitForSelector('.cl-ring', { timeout: 15000 });
    await b.click('.cl-ring .cl-big');
    const rb = await until(async () => { const r = await room(b); return r && r.inRoom && r; }, 15000);
    ok('B tritt bei, C klingelt noch (nicht angenommen)', rb && !(await room(c)), rb);
    await a.waitForTimeout(800);
    ok('A: Teilnehmerliste geoeffnet', await openList(a));
    const cfg = await api(a, 'GET', '/api/calls/config');
    const ringS = (await api(a, 'GET', '/api/calls/' + rb.id)).r.call.ring_s;

    if (OLD) {
      await until(async () => !(await c.$('.cl-ring')), (ringS + 5) * 1000, 500);
      await a.waitForTimeout(1500);
      await shots(a, '');
      ok('vorher: Bilder aufgenommen (kein Abschnitt "Nicht dabei")', !(await a.$('.cl-away')));
    } else {
      const r0 = await until(() => rowOf(a, NC), 8000);
      ok('A: Abschnitt "Nicht dabei (1)" mit C "klingelt …", ohne Knopf', r0 && /^Nicht dabei \(1\)$/.test(r0.head) && /klingelt/.test(r0.state) && r0.button === null, r0);
      ok('... B und A stehen nicht in "Nicht dabei"', await a.evaluate(() => document.querySelectorAll('.cl-away .cl-pl-away').length === 1));
      const r1 = await until(async () => { const r = await rowOf(a, NC); return r && /nicht angenommen/.test(r.state) && r; }, (ringS + 6) * 1000, 300);
      ok(`nach der Klingeldauer (${ringS} s) ohne Neuladen: "nicht angenommen" + Knopf "Erneut klingeln" mit Namen (aria-label)`,
         r1 && r1.button === 'Erneut klingeln' && r1.aria === NC + ' erneut klingeln lassen' && r1.disabled === false, r1 || await rowOf(a, NC));
      await shots(a, '');
      // Tastatur: Fokus auf den Knopf, Enter
      await openList(a);
      const mC = await c.evaluate(() => !!document.querySelector('.cl-ring'));
      await a.focus('.cl-away .cl-pl-ring'); await a.keyboard.press('Shift+Tab'); await a.keyboard.press('Tab');   // Tastatur-Fokus
      const fv = await a.evaluate(() => { const b = document.activeElement; return b && b.matches('.cl-pl-ring:focus-visible') && getComputedStyle(b).outlineStyle; });
      await a.keyboard.press('Enter');
      const r2 = await until(async () => { const r = await rowOf(a, NC); return r && /klingelt/.test(r.state) && r; }, 600, 30);
      ok(`Erneut klingeln per Tastatur (Fokus sichtbar: ${fv}): sofort "klingelt …"`, r2 && fv && fv !== 'none' && !mC, r2);
      const ringC = await until(() => c.$('.cl-ring'), 10000);
      ok('C klingelt wieder (Leiste)', !!ringC);
      await shot(c, 'c-klingelt-wieder');
      await c.click('.cl-ring .cl-red');
      const r3 = await until(async () => { const r = await rowOf(a, NC); return r && /abgelehnt/.test(r.state) && r; }, 8000, 200);
      ok('C lehnt ab -> bei A "hat abgelehnt" (Ereignis call.invites), Knopf wieder da', r3 && r3.button === 'Erneut klingeln', r3 || await rowOf(a, NC));
      // 429 nachgestellt: Zustand zurueck + Hinweis, Fokus bleibt
      await a.route('**/api/calls/*/invite', r => r.fulfill({ status: 429, contentType: 'application/json', body: JSON.stringify({ error: 'Zu viele Anrufe in kurzer Zeit – bitte kurz warten.' }) }));
      await a.focus('.cl-away .cl-pl-ring'); await a.keyboard.press('Enter');
      const toast = await until(() => a.evaluate(() => { const t = document.querySelector('.toast'); return t && !t.hidden && /zu oft/.test(t.textContent) && t.textContent; }), 4000);
      const r4 = await until(async () => { const r = await rowOf(a, NC); return r && /abgelehnt/.test(r.state) && r; }, 3000);
      const foc = await until(() => a.evaluate(() => !!(document.activeElement && document.activeElement.matches('.cl-pl-ring'))), 1500)
        || await a.evaluate(() => { const x = document.activeElement; return x ? x.tagName + '.' + x.className + ' ' + (x.dataset.k || '') : 'kein'; });
      ok('429: zurueck auf "hat abgelehnt", Hinweis "zu oft geklingelt", Fokus bleibt auf dem Knopf', toast && r4 && foc === true, { toast, r4, foc });
      await a.unroute('**/api/calls/*/invite');
      await shot(a, 'desktop-dunkel-abgelehnt');
      ok('keine Server-Konfiguration verbogen (Anrufe an)', cfg.ok && cfg.r.enabled);
    }
    for (const p of [b, a]) { const h = await p.$('.cl-ctl .cl-red'); if (h) await h.click().catch(() => {}); }
    await a.waitForTimeout(800);
  } catch (e) {
    console.log('FEHLER ' + (e && e.stack || e));
    res.push(false);
  } finally {
    for (const br of BROWSERS) await br.close().catch(() => {});
  }
  console.log(`\nBROWSER-ERNEUT-KLINGELN ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) && res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) && res.length ? 0 : 1);
})();
