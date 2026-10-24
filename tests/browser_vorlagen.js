// Browser-Test 2.5: eingebettete Datenbank - Titel/Ansichts-Reiter ausblendbar (je Block, bleibt nach dem Neuladen),
// Vorlagen verwalten (Datenbank: anlegen, umbenennen, Standard fuer "+ Neu", duplizieren, Papierkorb; Seitenvorlagen).
//   SAGA_BASE=... SAGA_USER=... SAGA_PASS=... [PLAYWRIGHT=...] node tests/browser_vorlagen.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function until(p, fn, arg, ms = 15000) {
  for (const t0 = Date.now(); Date.now() - t0 < ms; await p.waitForTimeout(100)) if (await p.evaluate(fn, arg)) return true;
  return false;
}
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1366, height: 900 }, locale: 'de-DE' });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => { console.log('SEITENFEHLER', e.message); res.push(false); });
  const made = [];
  try {
    await p.goto(BASE + '/login');
    await p.fill('input[name=username], input#username, input[type=text]', USER);
    await p.fill('input[type=password]', PASS);
    await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
    await p.waitForTimeout(800);
    const api = (m, u, b) => p.evaluate(async ([m, u, b]) => {
      const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
      return r.json().catch(() => ({}));
    }, [m, u, b]);
    const db = (await api('POST', '/api/notes', { kind: 'database', title: 'Bugs Vorlagentest' })).id; made.push(db);
    await api('POST', '/api/notes', { parent_id: db, title: 'Erster Bug' });
    const tp = (await api('POST', '/api/notes', { parent_id: db, title: 'Bugmeldung' })).id;
    await api('PATCH', '/api/notes/' + tp, { template: true });
    const pg = (await api('POST', '/api/notes', { title: 'Seite mit Bugs' })).id; made.push(pg);
    await api('PATCH', '/api/notes/' + pg, { format: 'blocks', body: JSON.stringify({ blocks: [{ id: 'd1', type: 'database', data: { db: db } }] }) });
    const open = async (id) => { await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector('.embed-db .db-bar, .db-bar'); await p.waitForTimeout(500); };

    // --- Kopf ausblenden -------------------------------------------------------------------------------------
    await open(pg);
    ok('Titel und Reiter sichtbar', await p.$('.embed-db .embed-title') && await p.$('.embed-db .db-tabs'));
    await p.click('.embed-db .db-gear');
    await p.waitForSelector('.vs-check');
    const boxes = await p.$$('.vs-check input');
    ok('Ansicht-Menue: zwei Schalter "Dieser Block"', boxes.length === 2);
    await boxes[0].click(); await p.waitForTimeout(200);
    await (await p.$$('.vs-check input'))[1].click();
    await p.keyboard.press('Escape');
    ok('Titel und Reiter ausgeblendet', await until(p, () => !document.querySelector('.embed-db .embed-title') && !document.querySelector('.embed-db .db-tabs')));
    ok('Rest der Leiste bleibt (Suche, ⚙, + Neu)', await p.$('.embed-db .db-search') && await p.$('.embed-db .db-gear') && await p.$('.embed-db .db-new-top'));
    await p.waitForTimeout(2500);
    const blk = JSON.parse((await api('GET', '/api/notes/' + pg)).body).blocks[0].data;
    ok('Im Block gespeichert (hide_title/hide_tabs)', blk.hide_title === true && blk.hide_tabs === true, blk);
    await open(pg);
    ok('Nach dem Neuladen weiter ausgeblendet', !(await p.$('.embed-db .embed-title')) && !(await p.$('.embed-db .db-tabs')));
    await open(db);
    ok('Datenbank-Seite selbst: Reiter weiter da', await p.$('.db-tabs'));

    // --- Vorlagen verwalten ----------------------------------------------------------------------------------
    await p.click('.db-new-top');
    await p.waitForSelector('.menu:not([hidden]) .menu-item');
    const items = await p.$$eval('.menu:not([hidden]) .menu-item', (a) => a.map((x) => x.textContent));
    ok('"+ Neu ▾" bietet "Vorlagen verwalten …"', items.some((t) => t.includes('Vorlagen verwalten')), items);
    await p.click('.menu:not([hidden]) .menu-item:has-text("Vorlagen verwalten")');
    await p.waitForSelector('.tpl-item');
    ok('Verwaltung listet die Vorlage', (await p.$$('.tpl-item')).length === 1 && (await p.inputValue('.tpl-name')) === 'Bugmeldung');
    await p.fill('.tpl-name', 'Bugmeldung neu'); await p.press('.tpl-name', 'Enter');
    ok('Umbenennen direkt im Feld', await until(p, async () => true) && await (async () => {
      for (let i = 0; i < 50; i++) { const r = await api('GET', '/api/notes/' + db + '/rows'); if ((r.templates || [])[0] && r.templates[0].title === 'Bugmeldung neu') return true; await p.waitForTimeout(200); }
      return false; })());
    await p.click('.tpl-more'); await p.click('.menu:not([hidden]) .menu-item:has-text("Als Standard")');
    await p.waitForSelector('.tpl-item .pg-badge');
    ok('Standard gesetzt (Abzeichen, Schema)', (await api('GET', '/api/notes/' + db)).schema.default_tpl === tp);
    await p.click('.tpl-more'); await p.click('.menu:not([hidden]) .menu-item:has-text("Duplizieren")');
    ok('Duplizieren: zwei Vorlagen', await until(p, () => document.querySelectorAll('.tpl-item').length === 2));
    const names = await p.$$eval('.tpl-name', (a) => a.map((x) => x.value));
    ok('Kopie heisst "… (Kopie)"', names.includes('Bugmeldung neu (Kopie)'), names);
    await (await p.$$('.tpl-more'))[1].click(); await p.click('.menu:not([hidden]) .menu-item:has-text("In den Papierkorb")');
    await p.click('.hk-ask .btn-destr');
    ok('Papierkorb nach Rueckfrage: wieder eine Vorlage', await until(p, () => document.querySelectorAll('.tpl-item').length === 1));
    await p.click('#pg-modal-close');
    await p.waitForTimeout(300);
    const before = (await api('GET', '/api/notes/' + db + '/rows')).rows.length;
    await p.click('.db-new-top');
    await p.click('.menu:not([hidden]) .menu-item:has-text("Leerer Eintrag")');
    await p.waitForTimeout(1200);
    const r1 = await api('GET', '/api/notes/' + db + '/rows');
    ok('"Leerer Eintrag" bleibt leer', r1.rows.length === before + 1 && r1.rows.some((r) => r.title === ''));
    await open(db);
    await p.click('.db-new-row').catch(async () => { await p.click('.db-gear'); });
    await p.waitForTimeout(1500);
    const r2 = await api('GET', '/api/notes/' + db + '/rows');
    ok('"+ Neu" unten nimmt die Standardvorlage', r2.rows.filter((r) => r.title === 'Bugmeldung neu').length === 1, r2.rows.map((r) => r.title));
    // Seitenvorlagen
    const sp = (await api('POST', '/api/notes', { title: 'Meine Seitenvorlage' })).id; made.push(sp);
    await api('PATCH', '/api/notes/' + sp, { template: true });
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + pg); await p.waitForTimeout(800);
    await p.click('#pg-new');
    await p.click('.menu:not([hidden]) .menu-item:has-text("Vorlagen verwalten")');
    await p.waitForSelector('.tpl-item');
    const pn = await p.$$eval('.tpl-name', (a) => a.map((x) => x.value));
    ok('Seitenvorlagen verwalten: listet eigene Seitenvorlage, keine Datenbank-Vorlage', pn.includes('Meine Seitenvorlage') && !pn.includes('Bugmeldung neu'), pn);
    await p.screenshot({ path: process.env.SHOT || '/tmp/vorlagen.png' });
  } catch (e) {
    ok('Ablauf', false, String(e && e.stack || e));
  } finally {
    for (const id of made) await p.evaluate((id) => fetch('/api/notes/' + id, { method: 'DELETE', headers: { 'X-Saga': '1' } }), id).catch(() => {});
    await br.close();
    const n = res.filter(Boolean).length;
    console.log(`\nVORLAGEN-BROWSER ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
    process.exit(n === res.length ? 0 : 1);
  }
})();
