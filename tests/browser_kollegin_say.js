// Kollegin-Bausteine (2.6, Bugs-DB 2268 K-H) im Web: Einstellungen › Saga (Anrede du/Sie, ruhigere Lebhaftigkeit) -
// Auswahl speichert ueber PUT /api/ai/prefs, "Standard (…)" setzt zurueck, Stufen nur bis zur Admin-Stufe; Admin › KI ›
// Persona zeigt Sprachform, Lebhaftigkeit, Standard-Anrede. Bilder nach SHOTS (optional). Keine Seiten-/CSP-Fehler.
// Lokal: HK_TEST_BASE=… HK_TEST_ADMIN=… CK=<Cookie> CKA=<Admin-Cookie> SHOTS=/tmp/x node tests/browser_kollegin_say.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, ADM = process.env.HK_TEST_ADMIN, CK = process.env.CK, CKA = process.env.CKA, SHOTS = process.env.SHOTS;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(150); } }
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  for (const scheme of ['light', 'dark']) {
    const ctx = await br.newContext({ viewport: { width: 1280, height: 860 }, locale: 'de-DE', timezoneId: 'Europe/Berlin', colorScheme: scheme });
    await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); const errs = [];
    p.on('pageerror', (e) => errs.push(e.message));
    p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused/.test(m.text())) errs.push(m.text()); });
    await p.goto(BASE + '/notes');
    const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
      const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
      return r.json().catch(() => ({}));
    }, [m, url, b]);
    await api('PUT', '/api/ai/prefs', { salutation: null, lively: null });
    await p.goto(BASE + '/notes?settings=saga');
    await p.waitForSelector('#hk-pane-saga:not([hidden]) #hk-saga-sal', { timeout: 10000 });
    await sleep(300);
    const nav = await p.locator('#hk-tab-saga').isVisible();
    ok(`[${scheme}] Navigation: Eintrag "Saga" unter Konto`, nav);
    const opts = await p.evaluate(() => [...document.querySelectorAll('#hk-saga-sal option')].map((o) => o.textContent));
    ok(`[${scheme}] Anrede: Standard (du) · du · Sie (${opts})`, opts.join('|') === 'Standard (du)|du|Sie', opts);
    const lv = await p.evaluate(() => [...document.querySelectorAll('#hk-saga-lively option')].map((o) => o.value));
    ok(`[${scheme}] Lebhaftigkeit nur bis zur Admin-Stufe (${lv})`, lv.join('|') === '|sachlich|warm', lv);
    if (SHOTS) await p.locator('.hk-dialog-set').screenshot({ path: `${SHOTS}/kh-einstellungen-saga-${scheme}.png` });
    await p.selectOption('#hk-saga-sal', 'sie');
    const d = await until(async () => { const r = await api('GET', '/api/ai/prefs'); return r.salutation === 'sie' ? r : null; });
    ok(`[${scheme}] Sie gewaehlt -> gespeichert`, d && d.effective.salutation === 'sie', d);
    const st = await until(async () => /Gespeichert/.test(await p.locator('#hk-pane-saga .hk-status').textContent()));
    ok(`[${scheme}] Rueckmeldung "✓ Gespeichert"`, st);
    await p.selectOption('#hk-saga-sal', '');
    const d2 = await until(async () => { const r = await api('GET', '/api/ai/prefs'); return r.salutation === null ? r : null; });
    ok(`[${scheme}] Standard gewaehlt -> zurueckgesetzt`, d2 && d2.effective.salutation === 'du', d2);
    ok(`[${scheme}] keine Seiten-/CSP-Fehler`, !errs.length, errs);
    await ctx.close();
    if (CKA && ADM) {
      const ac = await br.newContext({ viewport: { width: 1280, height: 1400 }, locale: 'de-DE', colorScheme: scheme });
      await ac.addCookies([{ name: 'hk_admin', value: CKA, domain: new URL(ADM).hostname, path: '/' }]);
      const ap = await ac.newPage();
      await ap.goto(ADM + '/settings/ai#persona');
      const has = await ap.evaluate(() => ['form', 'lively', 'salutation'].every((n) => document.querySelector('#persona select[name="' + n + '"]')));
      ok(`[${scheme}] Admin › KI › Persona: Sprachform, Lebhaftigkeit, Anrede`, has);
      if (SHOTS) await ap.locator('#persona').screenshot({ path: `${SHOTS}/kh-admin-persona-${scheme}.png` });
      await ac.close();
    }
  }
  await br.close();
  console.log(`\nBrowser Kollegin-Bausteine ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
