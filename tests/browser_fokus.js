// Saga 2.5 (Design-Runde, Barrierefreiheit 2) - Fokusfuehrung: Schnellsuche mit Fokusfalle und Rueckgabe, Menue schliesst
// beim Hinaus-Tabben, Board-Karte per Leertaste, Fokus nach dem Peek zurueck an die Karte, Inhaltsverzeichnis per Tastatur,
// Schublade am Handy modal (inert), Ansage-Region fuer Meldungen, DB-Zellen und -Reiter mit Zustand.
//   SAGA_BASE=… SAGA_USER=… SAGA_PASS=… node tests/browser_fokus.js   (Konto mit der Galerie-Vorlage "Aufgaben & Projekte")
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
(async () => {
  const b = await chromium.launch(), res = [], errs = [];
  const ok = (c, m) => { res.push([!!c, m]); console.log((c ? 'OK   ' : 'FAIL ') + m); };
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, locale: 'de-DE' });
  const p = await ctx.newPage(); p.on('pageerror', e => errs.push(String(e)));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  const ae = () => p.evaluate(() => { const a = document.activeElement; return a ? (a.id || a.className || a.tagName) + '' : ''; });
  // Seiten-ID der Board-Datenbank suchen
  const ids = await p.evaluate(async () => { const r = await fetch('/api/notes', { headers: { 'X-Saga': '1' } }); const d = await r.json(); const l = Array.isArray(d) ? d : d.notes;
    const page = l.find(x => x.kind !== 'database'); const db = l.find(x => x.kind === 'database' && /^(Tasks|Aufgaben)$/.test(x.title)); return { page: page && page.id, db: db && db.id }; });
  await p.goto(BASE + '/notes#' + ids.page); await p.waitForTimeout(1800);
  // --- Schnellsuche
  await p.focus('#pg-more'); await p.keyboard.press('Control+k'); await p.waitForTimeout(300);
  ok(await ae() === 'pg-qs-q', 'Schnellsuche: Fokus im Suchfeld');
  let inside = true; for (let i = 0; i < 14; i++) { await p.keyboard.press('Tab'); if (!(await p.evaluate(() => document.querySelector('.qs-box').contains(document.activeElement)))) inside = false; }
  ok(inside, 'Schnellsuche: Tab bleibt im Dialog');
  await p.keyboard.press('Escape'); await p.waitForTimeout(200);
  ok(await p.evaluate(() => document.getElementById('pg-qs').hidden), 'Schnellsuche: Esc schliesst (auch ausserhalb des Felds)');
  ok(await ae() === 'pg-more', 'Schnellsuche: Fokus zurueck am Ausloeser');
  // --- Menue schliesst beim Hinaus-Tabben
  await p.click('#pg-more'); await p.waitForTimeout(200);
  for (let i = 0; i < 40; i++) { await p.keyboard.press('Tab'); if (await p.evaluate(() => document.querySelector('.menu').hidden)) break; }
  ok(await p.evaluate(() => document.querySelector('.menu').hidden), 'Menue: schliesst, wenn der Fokus hinauslaeuft');
  // --- Inhaltsverzeichnis per Tastatur
  const toc = await p.evaluate(() => { const i = document.querySelector('.hk-toc-i'); if (!i) return null; i.focus(); const r = i.getBoundingClientRect(); return { w: Math.round(r.width), name: i.textContent.trim() }; });
  ok(!toc || (toc.w > 40 && toc.name), 'Inhaltsverzeichnis: bei Fokus aufgeklappt, Knopf mit Namen ' + JSON.stringify(toc));
  // --- Meldungen: Ansage-Region steht immer im Baum
  ok(await p.evaluate(() => !!document.querySelector('.sr-only[role=status][aria-live=polite]')), 'Meldungen: Ansage-Region vorhanden');
  // --- Board: Leertaste, Rueckgabe nach Peek; Zellen/Reiter
  if (ids.db) {
    await p.goto(BASE + '/notes#' + ids.db); await p.waitForTimeout(2000);
    ok(await p.evaluate(() => { const t = document.querySelector('.db-tabs'); return !!t && t.getAttribute('role') === 'group' && !!t.querySelector('[aria-pressed=true]'); }), 'DB-Reiter: Gruppe mit aktivem Zustand');
    const hasBoard = await p.$('.board-card');
    if (hasBoard) {
      ok(await p.evaluate(() => { const c = document.querySelector('.board-col'); return c.getAttribute('role') === 'group' && !!c.getAttribute('aria-label'); }), 'Board: Spalten als benannte Gruppe');
      const cid = await p.evaluate(() => { const c = document.querySelector('.board-card'); c.focus(); return c.dataset.id; });
      await p.keyboard.press(' '); await p.waitForTimeout(1500);
      ok(await p.evaluate(() => !document.getElementById('pg-peek').hidden), 'Board: Leertaste oeffnet den Eintrag');
      await p.click('#peek-close'); await p.waitForTimeout(2000);
      ok(await p.evaluate(id => { const a = document.activeElement; return !!a && a.classList.contains('board-card') && a.dataset.id === id; }, cid), 'Board: Fokus nach dem Schliessen zurueck an der Karte');
    } else console.log('--   kein Board in der ersten Ansicht – Board-Pruefungen uebersprungen');
    const lbl = await p.evaluate(() => { const v = document.querySelector('.val-btn[aria-label]'); return v ? v.getAttribute('aria-label') : null; });
    ok(!lbl || lbl.indexOf(':') > 0, 'DB-Zellen: Name nennt Spalte und Wert ' + lbl);
  }
  // --- Handy: Schublade modal
  const m = await (await b.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, locale: 'de-DE', storageState: await ctx.storageState() })).newPage();
  await m.goto(BASE + '/notes#' + ids.page); await m.waitForTimeout(1500);
  await m.click('#pg-side-btn'); await m.waitForTimeout(400);
  ok(await m.evaluate(() => document.getElementById('page').inert === true && !document.getElementById('pg-side').inert), 'Handy: offene Schublade macht den Inhalt inert');
  await m.keyboard.press('Escape'); await m.waitForTimeout(300);
  ok(await m.evaluate(() => document.getElementById('page').inert === false), 'Handy: geschlossen wieder bedienbar');
  ok(!errs.length, 'keine Skriptfehler ' + errs.join(' | '));
  await b.close();
  const bad = res.filter(r => !r[0]).length; console.log('\n' + (res.length - bad) + '/' + res.length + ' ok'); process.exit(bad ? 1 : 0);
})();
