// Browser-Reihe 2.6 (Bugs-DB 2390/2391): Ausnahme-Suche unter "Erreichbarkeit" (static/push.js excCard) mit einer Person
// eines verbundenen Servers - Treffer aus dessen Adressbuch waehlbar (Kennung '~x@host'), Eintrag und GET /api/account/status
// mit Namen samt Herkunft, nach Neuladen gleich; dazu HKPush.remoteRef gegen die Formen von GET /api/share/targets.
// Startet keine Server: tests/browser_status_fed.py baut die zwei Instanzen und ruft das hier auf.
//   BASE_A=http://host:port CK_A=<Cookie-Wert> HB=<host von B> FIND=bert FIND_NAME="Bert Gast" [SHOTS=/ordner]
//   [NUR_BILDER=1 (Vorher-Stand: nur Bilder, keine Pruefungen)] [BR=firefox] [PLAYWRIGHT=..] node tests/browser_status_fed.js
// Bilder: hell, schmal 390x844 und breit 1440x900 - <tag>-1-suche (Trefferliste), <tag>-2-liste (Eintrag nach Neuladen).
'use strict';
const pw = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path');
const E = process.env, BASE = E.BASE_A, HB = E.HB, FIND = E.FIND || 'bert', NAME = E.FIND_NAME || 'Bert Gast';
const NUR = E.NUR_BILDER === '1', FF = E.BR === 'firefox', SHOTS = E.SHOTS || '', BRN = FF ? 'firefox' : 'chromium';
if (!BASE || !E.CK_A || !HB) { console.error('BASE_A, CK_A, HB setzen'); process.exit(2); }
const res = [], errs = [];
function ok(name, cond, info) { if (NUR) return; res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' [' + BRN + '] ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 10000, step = 100) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn().catch(() => null); if (v) return v; await sleep(step); } return v; }
async function shot(p, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await p.waitForTimeout(300);
  await p.screenshot({ path: path.join(SHOTS, name + '.png') }).catch(e => console.log('--   Bild ' + name + ': ' + e.message));
}
const papi = (p, m, u, b) => p.evaluate(([m, u, b]) => fetch(u, { method: m, credentials: 'same-origin',
  headers: Object.assign({ 'X-Saga': '1' }, b !== undefined ? { 'Content-Type': 'application/json' } : {}), body: b !== undefined ? JSON.stringify(b) : undefined })
  .then(async r => ({ status: r.status, d: await r.json().catch(() => ({})) })), [m, u, b === undefined ? undefined : b]);
const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

async function reach(p) {
  await p.goto(BASE + '/dashboard?settings=reach'); await p.waitForSelector('#us-reach', { timeout: 15000 }); await p.waitForTimeout(500);
  await p.evaluate(() => { const q = document.getElementById('us-exc-q'); if (q) q.scrollIntoView({ block: 'center' }); });
}
const items = p => p.evaluate(() => [...document.querySelectorAll('#us-reach .us-exc-i .us-exc-t')].map(x => x.textContent));

async function runView(browser, tag, vp) {
  const ctx = await browser.newContext({ viewport: vp, locale: 'de-DE', colorScheme: 'light', timezoneId: 'Europe/Berlin' });
  await ctx.addCookies([{ name: 'saga', value: E.CK_A, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage();
  p.on('pageerror', e => { errs.push(e.message); console.log('  [pageerror] ' + e.message); });
  await reach(p);
  await papi(p, 'PUT', '/api/account/status/prefs', { exceptions: [] });
  await reach(p);
  // Suche: Treffer aus dem Adressbuch von B
  await p.fill('#us-exc-q', FIND);
  await until(() => p.evaluate(() => { const r = document.getElementById('us-exc-res'); return r && !r.hidden && r.children.length > 0; }), 8000);
  const opts = await p.evaluate(() => [...document.querySelectorAll('#us-exc-res [role=option]')].map(o => o.textContent));
  await shot(p, tag + '-1-suche');
  const hit = opts.findIndex(t => t.indexOf(NAME) >= 0);
  ok(tag + ': Person von B in der Trefferliste (' + JSON.stringify(opts) + ')', hit >= 0, opts);
  ok(tag + ': Host nicht doppelt (Name · Host ohne zweite Unterzeile)', hit >= 0 && opts[hit].split(HB).length === 2, opts[hit]);
  const want = NAME + ' (' + HB + ')';
  if (hit >= 0 && !NUR) {
    const w = p.waitForResponse(r => r.url().indexOf('/api/account/status/prefs') >= 0, { timeout: 8000 });
    for (let i = 0; i <= hit; i++) await p.press('#us-exc-q', 'ArrowDown');
    await p.press('#us-exc-q', 'Enter');
    const r = await w.catch(() => null);
    ok(tag + ': Speichern 200', r && r.status() === 200, r && r.status());
    await p.waitForTimeout(300);
    const li = await items(p);
    ok(tag + ': Eintrag mit Namen und Herkunft (' + JSON.stringify(li) + ')', li.length === 1 && li[0] === want, li);
  } else {
    // Vorher-Stand: Ausnahme direkt per API (Server nimmt '~x@host' schon an), damit die Liste zu sehen ist
    const t = await papi(p, 'GET', '/api/share/targets?q=' + encodeURIComponent(FIND));
    const h = ((t.d.remote || []).find(x => (x.name || '') === NAME) || {}).handle;
    if (h) await papi(p, 'PUT', '/api/account/status/prefs', { exceptions: [{ type: 'user', ref: h }] });
  }
  const g = await papi(p, 'GET', '/api/account/status');
  const ex = (g.d.exceptions || [])[0] || {};
  ok(tag + ': GET /api/account/status - Kennung ~x@host', new RegExp('^~[a-z2-7]{16}@' + esc(HB) + '$').test(ex.ref || ''), ex);
  ok(tag + ': GET /api/account/status - Name mit Herkunft (' + JSON.stringify(ex.name) + ')', ex.name === want, ex);
  ok(tag + ': kein Anmeldename in der Antwort', JSON.stringify(g.d).indexOf('fb-bert') < 0);
  await reach(p);
  const li2 = await items(p);
  ok(tag + ': nach Neuladen gleich', li2.length === 1 && li2[0] === want, li2);
  await shot(p, tag + '-2-liste');
  if (tag === 'breit') {
    // Formen von GET /api/share/targets (wie Reach2387Test der Android-App)
    const h16 = 'abcdefghijklmnop', R = await p.evaluate(h => {
      const f = window.HKPush && window.HKPush.remoteRef; if (!f) return null;
      return [f({ type: 'remote', target: '@~' + h + '@b.example', handle: '~' + h + '@b.example' }),
              f({ type: 'remote', target: '@anna@b.example', handle: '~' + h + '@b.example' }),
              f({ type: 'remote', target: '@~' + h + '@b.example' }),
              f({ type: 'remote', target: '@anna@b.example' }),
              f({ type: 'remote_group', target: 'team@b.example', handle: '~' + h + '@b.example' }),
              f({ target: '~' + h + '@b.example' }),
              f({ type: 'remote', handle: '~' + h }),
              f({ type: 'remote', handle: ' ~' + h + '@b.example ' }),
              f({ type: 'remote', handle: '~' + h + '@B.example' })];
    }, h16);
    const H = '~' + h16 + '@b.example';
    ok('remoteRef: Formen des Servers (' + JSON.stringify(R) + ')',
       JSON.stringify(R) === JSON.stringify([H, H, H, null, null, H, null, H, null]), R);
  }
  await papi(p, 'PUT', '/api/account/status/prefs', { exceptions: [] });
  await ctx.close();
}

(async () => {
  const browser = await (FF ? pw.firefox : pw.chromium).launch({ headless: true });
  try {
    await runView(browser, 'schmal', { width: 390, height: 844 });
    await runView(browser, 'breit', { width: 1440, height: 900 });
  } catch (e) {
    console.log('FEHLER ' + (e && e.stack || e)); res.push(false);
  } finally {
    await browser.close();
  }
  ok('keine Seitenfehler (' + errs.length + ')', errs.length === 0, errs.slice(0, 3));
  const good = NUR || (res.length > 0 && res.every(Boolean));
  console.log('\nBROWSER-STATUS-FED ' + res.filter(Boolean).length + '/' + res.length + ' OK – ' + (NUR ? 'NUR BILDER' : good ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(good ? 0 : 1);
})();
