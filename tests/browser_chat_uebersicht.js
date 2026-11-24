// Saga 2.6 (Bugs-DB 2030) - "Chats" ohne geoeffnete Unterhaltung zeigt eine Uebersicht statt nur des Schlosses:
// noch nicht eingerichtet -> "Chats auf diesem Geraet einrichten" + Knopf (fuehrt zur Einrichtung); eingerichtet ohne
// Unterhaltungen -> Satz + "Neue Unterhaltung"; mit Unterhaltungen -> die letzten (antippbar) auch bei eingeklappter
// Seitenleiste; die KI fuellt die Seite nicht (K10). Schmal (Handy) bleibt die Listenspalte zuerst.
// SAGA_BASE, SAGA_USER/SAGA_PASS, SAGA_USER2/SAGA_PASS2 (zwei Konten; Chat-Schluessel richtet der Test ein). SHOTS=/ordner
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const fs = require('fs'); const E = process.env; const BASE = E.SAGA_BASE || 'http://127.0.0.1:8099';
const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
const RK = u => require('os').tmpdir() + '/saga-browser-rk-' + BASE.replace(/[^a-z0-9]+/gi, '_') + '-' + u + '.txt';
const errs = [];
async function login(br, u, opts = {}) {
  const ctx = await br.newContext(Object.assign({ viewport: { width: 1440, height: 880 }, locale: 'de-DE' }, opts));
  const p = await ctx.newPage(); p.on('pageerror', e => { errs.push(e.message); console.log('[err ' + u + ']', e.message); });
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', u); await p.fill('input[name=password]', u === U2 ? E.SAGA_PASS2 : E.SAGA_PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]); return p;
}
async function keys(p, u) {
  await p.waitForTimeout(1500);
  if (await p.$('.ct-setup-go')) {
    await p.click('.ct-setup-go'); await p.waitForSelector('#ct-rk'); const rk = (await p.textContent('#ct-rk')).trim(); fs.writeFileSync(RK(u), rk);
    await p.click('.hk-actions .btn-primary'); await p.fill('.ct-rk-last', rk.slice(-4)); await p.click('.ct-setup-done');
    await p.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 60000 });
  } else if (await p.$('.ct-rk-go')) {
    await p.click('.ct-rk-go'); await p.fill('.ct-rk-in', fs.readFileSync(RK(u), 'utf8').trim()); await p.click('.ct-rk-ok');
    await p.waitForSelector('.ct-rk-ok', { state: 'detached', timeout: 60000 }).catch(async e => {
      console.log('[rk ' + u + '] ' + await p.evaluate(() => { const o = document.querySelector('.ct-rk-ok'); const d = o && o.closest('.ct-ov, .hk-ask, section, div');
        return d ? d.textContent.replace(/\s+/g, ' ').slice(0, 300) : '?'; }));
      throw e;
    });
  }
  await p.waitForTimeout(600);
}
const R = []; const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x || ''))); };
const none = p => p.evaluate(() => { const n = document.getElementById('ct-none');
  return { shown: !!n && !n.hidden && n.getClientRects().length > 0, text: n ? n.textContent : '', chats: n ? n.querySelectorAll('.ct-none-chat').length : 0,
           setup: !!(n && n.querySelector('.ct-none-setup')), neu: !!(n && n.querySelector('.ct-none-new')) }; });

(async () => {
  const br = await chromium.launch(E.PW_CHANNEL ? { channel: E.PW_CHANNEL } : {});
  const a = await login(br, U1);
  // --- noch nicht eingerichtet (Einrichtungs-Fenster geschlossen bzw. nicht da) ---
  await a.goto(BASE + '/chats'); await a.waitForTimeout(1500);
  const fresh = !!(await a.$('.ct-setup-go')) || !!(await a.$('.ct-rk-go'));   // Einrichtung bzw. neues Geraet: noch nicht bereit
  if (fresh) {
    const n0 = await none(a);
    ok('nicht eingerichtet: Übersicht sagt es und bietet die Einrichtung an', n0.setup && /Chats auf diesem Gerät einrichten/.test(n0.text) && !/Wähle links/.test(n0.text), n0);
  } else console.log('SKIP  nicht eingerichtet (Konto hat schon Schlüssel auf diesem Gerät)');
  await keys(a, U1);
  const b = await login(br, U2); await b.goto(BASE + '/chats'); await keys(b, U2);

  // --- eingerichtet, ggf. ohne Unterhaltungen ---
  await a.goto(BASE + '/chats'); await a.waitForTimeout(1500);
  let n1 = await none(a);
  ok('eingerichtet: Übersicht statt Schloss, mit „Neue Unterhaltung“', n1.shown && n1.neu && !n1.setup && /Chats/.test(n1.text), n1);
  ok('„Neue Unterhaltung“ öffnet den Dialog', await a.evaluate(() => { document.querySelector('.ct-none-new').click(); return true; })
     && await a.waitForSelector('.ct-ov', { timeout: 5000 }).then(() => true).catch(() => false));
  await a.keyboard.press('Escape'); await a.waitForTimeout(300);

  // --- mit Unterhaltung: die letzten erscheinen, auch bei eingeklappter Seitenleiste ---
  await a.goto(BASE + '/chats#u' + U2); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
  await a.fill('#ct-input', 'Hallo Übersicht'); await a.press('#ct-input', 'Enter'); await a.waitForTimeout(900);
  await a.goto(BASE + '/chats'); await a.waitForTimeout(1500);
  await a.evaluate(() => { const b = document.getElementById('pg-side-btn'); if (b && b.getAttribute('aria-pressed') === 'true') b.click(); });
  await a.waitForTimeout(500);
  n1 = await none(a);
  ok('mit Unterhaltung: letzte Chats in der Übersicht (auch Seitenleiste eingeklappt)', n1.shown && n1.chats >= 1 && /Wähle eine Unterhaltung/.test(n1.text), n1);
  if (E.SHOTS) await a.screenshot({ path: E.SHOTS + '/chat-uebersicht.png' });
  await a.click('.ct-none-chat'); await a.waitForTimeout(1200);
  ok('Eintrag öffnet die Unterhaltung', await a.evaluate(() => !document.getElementById('ct-chat').hidden && document.getElementById('ct-none').hidden));
  await a.evaluate(() => { const b = document.getElementById('pg-side-btn'); if (b && b.getAttribute('aria-pressed') === 'false') b.click(); });

  // --- schmal: Liste zuerst ---
  const m = await login(br, U1, { viewport: { width: 390, height: 800 } });
  await m.goto(BASE + '/chats'); await keys(m, U1); await m.goto(BASE + '/chats'); await m.waitForTimeout(1500);
  ok('schmal: Listenspalte sichtbar', await m.evaluate(() => { const l = document.getElementById('ct-list'); return !!l && l.getClientRects().length > 0; }));

  ok('keine Skriptfehler', errs.length === 0, errs);
  await br.close();
  const bad = R.filter(x => !x[1]);
  console.log(bad.length ? `\n${R.length - bad.length}/${R.length} - FEHLER` : `\nChat-Übersicht ${R.length}/${R.length} - ALLES GRUEN`);
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
