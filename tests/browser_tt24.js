// Browser-Pruefungen zur Testrunde 2.4 (Web): Editor (W7: schnelles "/Hinweis⏎", Zitat per Tastatur verlassen),
// Versionen (W5: Rueckfrage beim Wiederherstellen), Gravatar-Einwilligung (W1), keine Anmeldenamen im Teilen-Dialog (W4).
//   SAGA_BASE=... SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... [PLAYWRIGHT=...] node tests/browser_tt24.js
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_USER2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1200))); }
async function shot(p, n) { if (E.SHOTS) { fs.mkdirSync(E.SHOTS, { recursive: true }); await p.screenshot({ path: path.join(E.SHOTS, 'tt24-' + n + '.png') }); } }
async function until(fn, ms = 10000, step = 100) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

async function session(browser, user, pass) {
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 860 }, locale: 'de-DE' });
  const p = await ctx.newPage();
  p.on('pageerror', e => console.log('  [pageerror ' + user + '] ' + e.message));
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', pass);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, u, b]);
  p.blocks = async id => { const n = await p.api('GET', '/api/notes/' + id); return (JSON.parse(n.body || '{}').blocks || []).map(b => b.type + ':' + (b.data.text || '').replace(/<[^>]+>/g, '')); };
  p.open = async id => { await p.goto(BASE + '/notes#' + id); await p.waitForSelector('#pg-editor .ce-block'); await p.waitForTimeout(800); };
  return p;
}

(async () => {
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true });
  try {
    const a = await session(browser, E.SAGA_USER, E.SAGA_PASS);

    // --- W7: schnelles "/Hinweis⏎" und "/Tabelle⏎" -----------------------------------------------------------------
    for (const [cmd, typ] of [['/Hinweis', 'callout'], ['/Tabelle', 'table'], ['/Zitat', 'quote']]) {
      const id = (await a.api('POST', '/api/notes', { title: 'Schnell ' + cmd })).id;
      await a.open(id);
      await a.click('#pg-editor .ce-paragraph');
      if (cmd === '/Zitat') await a.keyboard.type(cmd, { delay: 15 });        // durchgehend schnell
      else { await a.keyboard.type('/'); await a.waitForTimeout(300); await a.keyboard.type(cmd.slice(1)); }   // wie die Testrunde: Filter ohne Pause
      await a.keyboard.press('Enter');
      await a.waitForTimeout(400);
      await a.keyboard.type('Inhalt', { delay: 10 });
      await a.waitForTimeout(2200);
      const b = await a.blocks(id);
      ok(`W7: schnell getipptes "${cmd}⏎" ergibt den Block ${typ}`, b.some(x => x.startsWith(typ + ':')) && !b.some(x => x.indexOf(cmd) >= 0), b);
    }

    // --- W7: Zitat per Tastatur verlassen ---------------------------------------------------------------------------
    let id = (await a.api('POST', '/api/notes', { title: 'Zitat verlassen' })).id;
    await a.api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks: [{ type: 'quote', data: { text: 'Sein oder nicht sein', caption: '', alignment: 'left' } }] }), format: 'blocks' });
    await a.open(id);
    await a.click('#pg-editor .cdx-quote__text'); await a.keyboard.press('End');
    await a.keyboard.press('Enter'); await a.keyboard.press('Enter');
    await a.keyboard.type('Danach', { delay: 10 }); await a.waitForTimeout(2200);
    let b = await a.blocks(id);
    ok('W7: Enter in leerer letzter Zeile verlaesst das Zitat (neuer Absatz darunter)', b.length === 2 && b[0] === 'quote:Sein oder nicht sein' && b[1] === 'paragraph:Danach', b);
    await a.click('#pg-editor .cdx-quote__text'); await a.keyboard.press('End');
    await a.keyboard.press('Shift+Enter'); await a.keyboard.type('Zeile 2', { delay: 10 });
    await a.waitForTimeout(1500);
    b = await a.blocks(id);
    ok('W7: Shift+Enter bleibt im Zitat', b.length === 2 && /Zeile 2/.test(b[0]), b);
    id = (await a.api('POST', '/api/notes', { title: 'Zitat Pfeil' })).id;
    await a.api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks: [{ type: 'quote', data: { text: 'Am Ende', caption: '', alignment: 'left' } }] }), format: 'blocks' });
    await a.open(id);
    await a.click('#pg-editor .cdx-quote__text'); await a.keyboard.press('End');
    await a.keyboard.press('ArrowDown'); await a.keyboard.type('Unten', { delay: 10 }); await a.waitForTimeout(2200);
    b = await a.blocks(id);
    ok('W7: Pfeil nach unten am Ende des letzten Zitats -> Absatz darunter', b.length === 2 && b[1] === 'paragraph:Unten', b);

    // --- W5: Wiederherstellen mit Rueckfrage -------------------------------------------------------------------------
    id = (await a.api('POST', '/api/notes', { title: 'Versionen' })).id;
    await a.api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks: [{ type: 'paragraph', data: { text: 'Alt' } }] }), format: 'blocks' });
    await a.api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks: [{ type: 'paragraph', data: { text: 'Neu und wichtig' } }] }), format: 'blocks' });
    await a.open(id);
    await a.click('#pg-more');
    await a.click('text=Versionsverlauf');
    await a.waitForSelector('.ver-item', { timeout: 8000 });
    await a.click('.ver-item');
    await a.waitForSelector('.ver-prev .btn');
    await a.click('.ver-prev .btn');
    const ask = await until(() => a.evaluate(() => { const x = document.querySelector('.hk-ask'); return x && x.textContent; }), 4000);
    ok('W5: "Wiederherstellen" fragt nach (jetziger Stand wird gesichert)', ask && /gesichert/.test(ask), ask);
    await shot(a, 'w5-rueckfrage');
    await a.keyboard.press('Escape');
    await a.waitForTimeout(600);
    ok('W5: Abbrechen laesst den Inhalt unveraendert', (await a.blocks(id)).join() === 'paragraph:Neu und wichtig');

    // --- W1: Gravatar ab Werk aus, Einschalten mit Hinweis -----------------------------------------------------------
    const av = await a.api('GET', '/api/account/avatar');
    ok('W1: Gravatar fuer ein neues Konto aus', av.gravatar === false, av);

    // --- W4: Teilen-Dialog ohne Anmeldenamen -------------------------------------------------------------------------
    const tg = await a.api('GET', '/api/share/targets?q=' + encodeURIComponent(E.SAGA_USER2));
    const u2 = (tg.users || [])[0] || {};
    await a.api('PUT', '/api/notes/' + id + '/shares', { type: 'user', target: u2.username, role: 'view' });
    await a.open(id);
    await a.evaluate(() => { const b = [...document.querySelectorAll('button')].find(x => /Teilen/.test(x.textContent || x.title || '')); if (b) b.click(); });
    const dlg = await until(() => a.evaluate(() => { const l = document.querySelector('.sh-item'); return l && document.querySelector('.sh-item').closest('div').textContent; }), 6000);
    ok('W4: Teilen-Dialog zeigt den Anmeldenamen der anderen Person nicht', dlg && dlg.indexOf(E.SAGA_USER2) < 0 && /Person/.test(dlg), dlg);
    await shot(a, 'w4-teilen');
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(`\nBROWSER-TT24 ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
