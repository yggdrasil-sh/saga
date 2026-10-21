// Browser-Ende-zu-Ende fuer die Vertrauens-Uebernahme (2.5, static/chat.js fedKeys/h2u, X.tofuMigrate): ein Chat v1 wird auf
// Stufe 3 umgestellt; was A in v1 als "ueberprueft" markiert hatte, bleibt es (gleicher Identitaetsschluessel), die
// Sicherheitsnummer zeigt "Neue Darstellung, gleicher Schluessel", die Systemzeile zur Umstellung erscheint, alte
// Nachrichten (v1) und neue (v2) sind lesbar. Zwei Phasen in denselben Browser-Profilen (IndexedDB bleibt):
//   PHASE=1: chat_v3 aus - Chat v1 anlegen, Nachricht, A markiert B als ueberprueft
//   PHASE=2: (tests/browser_v3.py hat umgestellt) - pruefen wie oben, dann eine Nachricht im Format v2
// Umgebung wie die Browser-Tests plus PROFILES=<Ordner fuer die Browser-Profile>.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE, PHASE = E.PHASE || '1', PROF = E.PROFILES || path.join(os.tmpdir(), 'saga-trust3');
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '';
const res = [], errs = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, name + '.png') }); } }
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

async function session(user, pass) {
  const ctx = await chromium.launchPersistentContext(path.join(PROF, user), { channel: E.PW_CHANNEL || 'chrome', headless: true,
    viewport: { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: 'dark' });
  const p = ctx.pages()[0] || await ctx.newPage();
  p.on('pageerror', e => { errs.push(user + ': ' + e.message); console.log('  [pageerror ' + user + '] ' + e.message); });
  await p.goto(BASE + '/chats'); await p.waitForTimeout(1500);
  if (/\/login/.test(p.url())) {
    await p.fill('input[name=username], input#username, input[type=text]', user);
    await p.fill('input[type=password]', pass);
    await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
    await p.waitForTimeout(800);
  }
  return { ctx, p };
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
const texts = p => p.evaluate(() => [...document.querySelectorAll('#ct-msgs .ct-msg')].map(r => ({ t: (r.querySelector('.ct-text') || {}).textContent || '', unv: r.classList.contains('ct-unverified') })));

(async () => {
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  let A, B;
  try {
    A = await session(U1, E.SAGA_PASS); B = await session(U2, E.SAGA_PASS2);
    const a = A.p, b = B.p;
    if (PHASE === '1') {
      await chatKeys(a, U1); await chatKeys(b, U2);
      await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
      const v = await a.evaluate(() => fetch('/api/chats', { headers: { 'X-Saga': '1', 'X-Saga-Chat': '3' } }).then(r => r.json()).then(d => d.chats.map(c => ({ v: c.v, h: c.members.map(m => !!m.handle) }))));
      ok('Phase 1: Chat v1, Mitglieder mit Handle (fuer die Uebernahme)', v.length === 1 && !(v[0].v >= 2) && v[0].h.every(Boolean), v);
      await a.fill('#ct-input', 'Alte Nachricht (v1)'); await a.press('#ct-input', 'Enter'); await a.waitForTimeout(800);
      await b.goto(BASE + '/chats'); await b.waitForTimeout(1200); await b.click('#ct-list .ct-item-b'); await b.waitForTimeout(1200);
      ok('Phase 1: B hat gelesen - A sieht ✓✓', await until(() => a.evaluate(() => !!document.querySelector('#ct-msgs .ct-tick.ct-read')), 10000));
      await a.click('#ct-more'); await a.waitForSelector('.menu .menu-item');
      await a.click('.menu .menu-item:has-text("Mitglieder")');
      await a.waitForSelector('.ct-dev button:has-text("Sicherheitsnummer")'); await a.click('.ct-dev button:has-text("Sicherheitsnummer")');
      await a.waitForSelector('#ct-safety'); await a.click('.ct-ov .btn-primary:has-text("Als überprüft markieren")');
      ok('Phase 1: A hat B als ueberprueft markiert (Kopf)', await until(() => a.evaluate(() => !!document.querySelector('#ct-sub .ct-verified')), 5000));
      const h2u = await a.evaluate(() => new Promise(res => { const r = indexedDB.open('saga-chat'); r.onsuccess = () => {
        try { const st = r.result.transaction('kv').objectStore('kv'), g = st.getAllKeys(); g.onsuccess = () => res(g.result.filter(k => /^h2u:|^trust/.test(k))); } catch (e) { res(String(e)); } };
        r.onerror = () => res('idb'); }));
      ok('Phase 1: Handle -> Konto gemerkt (h2u) und Vertrauen v1', Array.isArray(h2u) && h2u.some(k => /^h2u:/.test(k)) && h2u.some(k => /^trust:/.test(k)), h2u);
      // 2.5 (Testrunde Web Nr. 063): Vertrauen verschluesselt auf dem Server gesichert (fuer die Wiederherstellung)
      ok('Phase 1: Vertrauen verschluesselt gesichert (GET /api/chat/trust)', await until(() => a.evaluate(() => fetch('/api/chat/trust', { headers: { 'X-Saga': '1' } })
        .then(r => r.json()).then(d => !!d.blob && !/verified|ident/.test(d.blob))), 10000));
    } else {
      await a.goto(BASE + '/chats'); await a.waitForTimeout(1500); await a.click('#ct-list .ct-item-b'); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);
      const v = await a.evaluate(() => fetch('/api/chats', { headers: { 'X-Saga': '1', 'X-Saga-Chat': '3' } }).then(r => r.json()).then(d => d.chats.map(c => ({ v: c.v, v3: c.v3 }))));
      ok('Phase 2: Chat umgestellt (v 3, v3.seq)', v.length === 1 && v[0].v === 3 && v[0].v3 && v[0].v3.seq > 0, v);
      ok('Phase 2: Systemzeile zur Umstellung', await until(() => a.evaluate(() => [...document.querySelectorAll('.ct-sys')].some(x => /stabilen Saga-Adressen/.test(x.textContent))), 5000));
      ok('Phase 2: "ueberprueft" uebernommen (gleicher Schluessel)', await until(() => a.evaluate(() => !!document.querySelector('#ct-sub .ct-verified')), 8000));
      ok('Phase 2: keine Warnung', await a.evaluate(() => document.querySelector('#ct-warn').hidden));
      await a.fill('#ct-input', 'Neue Nachricht (v2)'); await a.press('#ct-input', 'Enter'); await a.waitForTimeout(1200);
      const ta = await texts(a);
      ok('Phase 2: alte Nachricht (v1) und neue (v2) lesbar und bestaetigt', ta.some(x => x.t === 'Alte Nachricht (v1)' && !x.unv) && ta.some(x => x.t === 'Neue Nachricht (v2)' && !x.unv), ta);
      // 2.5 (Testrunde Web Nr. 054): B hatte die alte Nachricht vor der Umstellung gelesen - sie behaelt ihr ✓✓
      const tick = () => a.evaluate(() => [...document.querySelectorAll('#ct-msgs .ct-msg')].filter(r => /Alte Nachricht \(v1\)/.test(r.textContent))
        .map(r => { const t = r.querySelector('.ct-tick'); return t ? t.className + ' ' + t.textContent : '-'; })[0]);
      ok('Phase 2: alte, gelesene Nachricht behaelt ✓✓ nach der Umstellung (Nr. 054)', await until(async () => /ct-read/.test(await tick() || ''), 8000), await tick());
      await shot(a, 'trust3-1-umgestellt');
      await a.click('#ct-more'); await a.waitForSelector('.menu .menu-item');
      await a.click('.menu .menu-item:has-text("Mitglieder")');
      await a.waitForSelector('.ct-dev button:has-text("Sicherheitsnummer")'); await a.click('.ct-dev button:has-text("Sicherheitsnummer")');
      await a.waitForSelector('#ct-safety');
      const sd = await a.evaluate(() => ({ mig: !!document.querySelector('.ct-ov .ct-migrated'), okv: !!document.querySelector('.ct-ov .ct-ok'), host: /Server-übergreifenden/.test(document.querySelector('.ct-ov').textContent) }));
      ok('Phase 2: Sicherheitsnummer "Neue Darstellung, gleicher Schluessel", als ueberprueft, ohne Server-Hinweis', sd.mig && sd.okv && !sd.host, sd);
      await shot(a, 'trust3-2-sicherheitsnummer');
      await b.goto(BASE + '/chats'); await b.waitForTimeout(1500); await b.click('#ct-list .ct-item-b'); await b.waitForTimeout(1500);
      const tb = await texts(b);
      ok('Phase 2: B liest beide (v1 und v2)', tb.some(x => x.t === 'Alte Nachricht (v1)' && !x.unv) && tb.some(x => x.t === 'Neue Nachricht (v2)' && !x.unv), tb);
      // 2.5 (Testrunde Web Nr. 063): neuer Browser, Wiederherstellungsschluessel -> "ueberprueft" ist wieder da
      const nb = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true });
      try {
        const nc = await nb.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE' }), n = await nc.newPage();
        n.on('pageerror', e => { errs.push(U1 + ' (neu): ' + e.message); });
        await n.goto(BASE + '/login');
        await n.fill('input[name=username], input#username, input[type=text]', U1); await n.fill('input[type=password]', E.SAGA_PASS);
        await Promise.all([n.waitForLoadState('networkidle').catch(() => {}), n.press('input[type=password]', 'Enter')]); await n.waitForTimeout(800);
        await chatKeys(n, U1);
        await n.goto(BASE + '/chats'); await n.waitForTimeout(1500); await n.click('#ct-list .ct-item-b'); await n.waitForSelector('#ct-chat:not([hidden])');
        ok('Wiederherstellung auf neuem Browser: "ueberprueft" bleibt (Nr. 063)', await until(() => n.evaluate(() => !!document.querySelector('#ct-sub .ct-verified')), 10000),
          await n.evaluate(() => document.querySelector('#ct-sub').textContent));
        await shot(n, 'trust3-3-wiederhergestellt');
        // Geraet wieder abmelden (sonst klingeln spaetere Anruf-Tests auch dort)
        const did = await n.evaluate(() => new Promise(res => { const r = indexedDB.open('saga-chat'); r.onsuccess = () => {
          const g = r.result.transaction('kv').objectStore('kv').getAll(); g.onsuccess = () => res((g.result.find(x => x && x.device) || {}).device.id); }; }));
        if (did) await n.evaluate(id => fetch('/api/chat/devices/' + id, { method: 'DELETE', headers: { 'X-Saga': '1' } }), did);
      } finally { await nb.close(); }
    }
    ok('keine Seitenfehler', errs.length === 0, errs);
  } catch (e) { console.log('FAIL Ausnahme: ' + (e && e.stack || e)); res.push(false); }
  if (A) await A.ctx.close(); if (B) await B.ctx.close();
  const n = res.filter(Boolean).length;
  console.log('\nBROWSER-TRUST3 ' + PHASE + ' ' + n + '/' + res.length + ' - ' + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
