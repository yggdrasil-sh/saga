// Browser-Regressionstests fuer die Weboberflaeche (Playwright, headless Chrome) - laufen NICHT im Stack-Testlauf,
// sondern von einem Rechner mit Playwright gegen einen laufenden Test-Stack:
//   SAGA_BASE=http://host:8175 SAGA_USER=konto SAGA_PASS=... [PLAYWRIGHT=/pfad/zu/node_modules/playwright-core] node tests/browser_web.js
// Das Konto braucht nichts Besonderes (legt eigene Seiten/Datenbanken an). Ausgabe wie die Python-Tests: OK/FAIL je Pruefung.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }

async function session(browser, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1366, height: 860 }, locale: opts.locale || 'de-DE',
    isMobile: !!opts.mobile, hasTouch: !!opts.mobile });
  const p = await ctx.newPage();
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', USER);
  await p.fill('input[type=password]', PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(1000);
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, u, b]);
  p.blocks = async id => { const n = await p.api('GET', '/api/notes/' + id); return (JSON.parse(n.body || '{}').blocks || []).map(b => b.type + ':' + (b.data.text || '')); };
  p.open = async id => { await p.goto(BASE + '/notes#' + id); await p.waitForSelector('#pg-editor .ce-paragraph, .db-row, .cal-grid'); await p.waitForTimeout(800); };
  return p;
}

// BUG-011: Enter, das Editor.js nicht selbst behandelt (Fehlerzustand nach "/Ueberschrift", Handy-Tastatur), darf kein
// <div> in den Block setzen (beim Speichern verworfen -> "KuchenMehl"), sondern teilt den Block.
async function enterFallback(p) {
  const id = (await p.api('POST', '/api/notes', { title: 'Enter-Test' })).id;
  await p.open(id);
  await p.click('#pg-editor .ce-paragraph');
  await p.keyboard.type('/Überschrift', { delay: 40 }); await p.keyboard.press('Enter');
  await p.keyboard.type('Kuchen', { delay: 30 });
  await p.evaluate(() => { window.__blk = e => { if (e.key === 'Enter') e.stopPropagation(); }; window.addEventListener('keydown', window.__blk, true); });
  await p.keyboard.press('Enter'); await p.keyboard.type('Mehl', { delay: 30 });
  await p.keyboard.press('ArrowLeft'); await p.keyboard.press('ArrowLeft'); await p.keyboard.press('Enter');
  await p.evaluate(() => window.removeEventListener('keydown', window.__blk, true));
  await p.keyboard.type('Zucker', { delay: 30 }); await p.waitForTimeout(2200);
  const b = await p.blocks(id);
  ok('BUG-011 Enter ohne Editor.js-Behandlung teilt den Block', JSON.stringify(b) === JSON.stringify(['header:Kuchen', 'paragraph:Me', 'paragraph:Zuckerhl']), b);
}

// BUG-012: Neu laden direkt nach dem Tippen (Seite + Namenszelle einer Datenbank) verliert nichts
async function reloadKeeps(p) {
  const id = (await p.api('POST', '/api/notes', { title: 'Reload-Test' })).id;
  await p.open(id);
  await p.click('#pg-editor .ce-paragraph'); await p.keyboard.type('Samstag: Markt', { delay: 15 });
  await p.reload(); await p.waitForTimeout(1500);
  ok('BUG-012 Seite: Text nach sofortigem Neuladen gespeichert', (await p.blocks(id)).some(x => x.includes('Samstag: Markt')));
  const D = (await p.api('POST', '/api/notes', { kind: 'database', title: 'Reload-DB' })).id;
  const R = (await p.api('POST', '/api/notes', { parent_id: D, title: 'Kita' })).id;
  await p.open(D);
  await p.locator('.db-row .db-title-in').first().click(); await p.keyboard.press('End'); await p.keyboard.type(' Y');
  await p.reload(); await p.waitForTimeout(1500);
  ok('BUG-012 Datenbank-Zelle: Name nach sofortigem Neuladen gespeichert', (await p.api('GET', '/api/notes/' + R)).title === 'Kita Y');
}

// BUG-013: Zahlen in der Sprache der Oberflaeche ("1.200" de = 1200, "1,200" en = 1200)
async function numbers(p, lang) {
  const de = lang === 'de';
  const unit = await p.evaluate(() => [['1.200'], ['1,5'], ['1.234.567,25'], ['1,234,567.25'], ['2.5'], ['-3'], ['12,'], ['abc'], ['1.2.3'], ['1 200']]
    .map(([s]) => [s, HK.parseNum(s)]));
  const want = de ? [1200, 1.5, 1234567.25, 1234567.25, 2.5, -3, 12, NaN, NaN, 1200] : [1.2, 1.5, 1234567.25, 1234567.25, 2.5, -3, 12, NaN, NaN, 1200];
  // en: "1,5" ist kein Tausender (keine 3er-Gruppe) -> Komma als Dezimalzeichen geduldet; "1.200" = 1,2
  ok('BUG-013 parseNum ' + lang, unit.every(([s, v], i) => Number.isNaN(want[i]) ? v === null || Number.isNaN(v) : v === want[i]), unit);
  const D = (await p.api('POST', '/api/notes', { kind: 'database', title: 'Zahlen ' + lang }));
  const s = (await p.api('GET', '/api/notes/' + D.id)).schema; s.props.push({ id: 'num', name: 'Betrag', type: 'number' });
  await p.api('PATCH', '/api/notes/' + D.id, { schema: s });
  const R = (await p.api('POST', '/api/notes', { parent_id: D.id, title: 'Miete' })).id;
  await p.open(D.id);
  const cell = p.locator('.db-row .val-number').first(), inp = cell.locator('input');
  await inp.click(); await p.keyboard.type(de ? '1.200' : '1,200'); await inp.press('Enter'); await p.waitForTimeout(700);
  ok('BUG-013 Zelle "' + (de ? '1.200' : '1,200') + '" = 1200 (' + lang + ')', ((await p.api('GET', '/api/notes/' + R)).props || {}).num === 1200);
}

// BUG-014: Konflikt-Kasten bleibt bei gescrollter Seite unter der festen Kopfleiste sichtbar
async function conflictVisible(p) {
  const blocks = Array.from({ length: 30 }, (_, i) => ({ id: 'b' + i, type: 'paragraph', data: { text: 'Absatz ' + i } }));
  const id = (await p.api('POST', '/api/notes', { title: 'Konflikt-Test' })).id;
  await p.api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks }), format: 'blocks' });
  await p.open(id);
  await p.locator('#pg-editor .ce-paragraph').nth(4).click(); await p.mouse.wheel(0, 300); await p.waitForTimeout(300);
  const g = await p.api('GET', '/api/notes/' + id);
  await p.api('PATCH', '/api/notes/' + id, { title: 'Konflikt-Test 2', rev: g.rev });
  await p.keyboard.type(' neu'); await p.waitForTimeout(2500);
  const m = await p.evaluate(() => { const c = document.getElementById('pg-conflict'), h = document.querySelector('.np-head');
    return c && { top: c.getBoundingClientRect().top, head: h ? h.getBoundingClientRect().bottom : 0 }; });
  ok('BUG-014 Konflikt-Kasten sichtbar (nicht unter der Kopfleiste)', m && m.top >= m.head - 1, m);
}

// BUG-015: Seitenkopf auf Handy (390 px) und Tablet (820 px) ohne Ueberlauf
async function narrow(browser) {
  for (const [name, vp, mobile] of [['Handy', { width: 390, height: 844 }, true], ['Tablet', { width: 820, height: 1180 }, false]]) {
    const p = await session(browser, { viewport: vp, mobile });
    const id = (await p.api('POST', '/api/notes', { title: 'Projektplan Sommerfest mit langem Namen' })).id;
    await p.api('POST', '/api/notes/' + id + '/comments', { block_id: 'x', body: 'Kommentar' }).catch(() => {});
    await p.open(id); await p.waitForTimeout(700);
    const m = await p.evaluate(() => ({ vw: innerWidth, sw: document.documentElement.scrollWidth,
      more: Math.round(document.getElementById('pg-more').getBoundingClientRect().right) }));
    ok('BUG-015 ' + name + ': kein Ueberlauf, Seitenmenue erreichbar', m.vw === vp.width && m.sw <= m.vw && m.more <= m.vw, m);
    await p.context().close();
  }
}

// BUG-047: sofort nach "+ Neue Seite" > "Leere Seite" getippter Titel geht nicht verloren
async function typeAheadTitle(p) {
  await p.goto(BASE + '/notes'); await p.waitForSelector('#pg-new'); await p.waitForTimeout(800);
  await p.click('#pg-new'); await p.waitForTimeout(150);
  await p.locator('.menu .menu-item, .menu button').filter({ hasText: /Leere Seite/ }).first().click();
  await p.keyboard.type('Rezepte', { delay: 70 }); await p.waitForTimeout(2500);
  const id = await p.evaluate(() => parseInt(location.hash.slice(1), 10));
  ok('BUG-047 Titel sofort nach "Leere Seite" vollstaendig', (await p.api('GET', '/api/notes/' + id)).title === 'Rezepte');
}

// BUG-056: Link auf eigene Seite im Papierkorb -> Hinweis + Wiederherstellen (Unterseite: Hinweis auf den Papierkorb)
async function trashLink(p) {
  const P = (await p.api('POST', '/api/notes', { title: 'Papierkorb-Eltern' })).id;
  const K = (await p.api('POST', '/api/notes', { title: 'Papierkorb-Kind', parent_id: P })).id;
  await p.api('DELETE', '/api/notes/' + P);
  const box = async id => { await p.goto(BASE + '/notes#' + id); await p.waitForTimeout(2000);
    return p.evaluate(() => { const b = document.getElementById('pg-noaccess'); return b && !b.hidden ? b.textContent : ''; }); };
  ok('BUG-056 Unterseite: Hinweis auf den Papierkorb', /Papierkorb/.test(await box(K)));
  ok('BUG-056 Seite selbst: "liegt im Papierkorb" + Wiederherstellen', /liegt im Papierkorb/.test(await box(P)) && await p.locator('#pg-noaccess .pg-na-restore').isVisible());
  await p.click('#pg-noaccess .pg-na-restore'); await p.waitForTimeout(1500);
  ok('BUG-056 Wiederherstellen oeffnet die Seite', (await p.inputValue('#pg-title')) === 'Papierkorb-Eltern');
}

(async () => {
  const browser = await chromium.launch({ channel: process.env.PW_CHANNEL || 'chrome', headless: true });
  try {
    const p = await session(browser);
    await enterFallback(p);
    await reloadKeeps(p);
    await numbers(p, 'de');
    await conflictVisible(p);
    await typeAheadTitle(p);
    await trashLink(p);
    await p.context().close();
    await narrow(browser);
  } catch (e) { console.error(e); res.push(false); }
  await browser.close();
  console.log(`${res.filter(Boolean).length}/${res.length} ok`);
  process.exit(res.every(Boolean) ? 0 : 1);
})();
