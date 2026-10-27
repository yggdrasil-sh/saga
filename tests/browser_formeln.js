// Browser-Test Formel-Editor und Paritaet (2.5 Paket 1): wie tests/browser_db.js gegen einen laufenden Test-Server:
//   SAGA_BASE=http://127.0.0.1:8110 SAGA_USER=konto SAGA_PASS=... NODE_PATH=$(npm root -g) PLAYWRIGHT=playwright PW_CHANNEL=chromium node tests/browser_formeln.js
// Prueft: neue Spalte "Formel" oeffnet den Editor, Eingabe mit Namen -> Vorschau vom Server, Fehler mit Position,
// Speichern legt IDs ins Schema (Namen bleiben im Editor sichtbar), Werte in der Tabelle, #Fehler mit Erklaerung,
// sofortige Aktualisierung nach Bearbeiten (PATCH-Antwort calc), Board nach Formel (Spalten = Werte, nur lesend, Mo..So),
// Paritaet: Reihenfolge Web (Tabelle gruppiert + Filter/Sortierung auf Formel) = Server (GET .../rows mit view).
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core'), ENGINE = process.env.PW_BROWSER || 'chromium';
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 600))); }

async function session(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 860 }, locale: 'de-DE', timezoneId: 'Europe/Berlin' });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => { console.log('SEITENFEHLER', e.message); res.push(false); });
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', USER);
  await p.fill('input[type=password]', PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(1000);
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, u, b]);
  p.open = async (id) => { await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector('.db-row, .dbl-row, .board-card, .db-new-row'); await p.waitForTimeout(700); };
  return p;
}
async function until(p, fn, arg, ms = 15000) {      // wie waitForFunction, aber ohne eval (Saga erlaubt kein 'unsafe-eval')
  for (const t0 = Date.now(); Date.now() - t0 < ms; await p.waitForTimeout(100)) if (await p.evaluate(fn, arg)) return true;
  return false;
}
function view(p, D, vid) { return p.evaluate(([D, vid]) => { localStorage.setItem('hk-db-view-' + D, vid); }, [D, vid]); }

(async () => {
  const browser = await PW[ENGINE].launch({ headless: true, channel: ENGINE === 'chromium' ? (process.env.PW_CHANNEL || 'chrome') : undefined });
  let D = null, p = null;
  try {
    p = await session(browser);
    D = (await p.api('POST', '/api/notes', { kind: 'database', title: 'Formel-Test' })).id;
    const s = (await p.api('GET', '/api/notes/' + D)).schema;
    s.props = [{ id: 'menge', name: 'Menge', type: 'number' }, { id: 'preis', name: 'Preis', type: 'number' }, { id: 'd', name: 'Datum', type: 'date' }];
    s.views = [{ id: 'tab', name: 'Tabelle', type: 'table' }];
    await p.api('PATCH', '/api/notes/' + D, { schema: s });
    const days = ['2026-10-26', '2026-10-28', '2026-11-01', '2026-10-27', '2026-10-26', null];
    for (let i = 0; i < 6; i++) {
      const props = { menge: i, preis: 2.5 }; if (days[i]) props.d = days[i];
      await p.api('POST', '/api/notes', { parent_id: D, title: 'Zeile ' + i, props });
    }
    await view(p, D, 'tab'); await p.open(D);

    // --- Neue Spalte "Formel" -> Editor
    await p.click('button.db-add-col');
    ok('Menue „Neue Spalte“ offen', await until(p, () => !!document.querySelector('.menu:not([hidden]) .menu-item')));
    const fitem = await p.$('.menu:not([hidden]) .menu-item:has-text("Formel")');
    ok('Spaltenmenue bietet „Formel“ an', !!fitem);
    if (fitem) await fitem.click();
    ok('Formel-Editor oeffnet sich', await until(p, () => !!document.querySelector('#pg-modal:not([hidden]) .fx-in')));
    await p.fill('.fx-in', 'prop("Preis") * prop("Menge")');
    ok('Vorschau vom Server: Ergebnis Zahl, Werte der ersten Zeilen', await until(p, () => {
      const st = document.querySelector('.fx-status'), pv = [...document.querySelectorAll('.fx-pv')].map((x) => x.textContent);
      return st && /Zahl/.test(st.textContent) && pv.length >= 3 && pv[1] === '2,5';
    }), await p.evaluate(() => [document.querySelector('.fx-status').textContent, [...document.querySelectorAll('.fx-pv')].map((x) => x.textContent)]));
    await p.fill('.fx-in', 'prop("Preis") *');
    ok('Fehler mit Position (Zeichen 16)', await until(p, () => /Zeichen 16/.test((document.querySelector('.fx-status.bad') || {}).textContent || '')),
      await p.evaluate(() => document.querySelector('.fx-status').textContent));
    await p.fill('.fx-in', 'prop("Preis") * prop("Menge")');
    await p.waitForTimeout(400);
    await p.click('.fx-save');
    ok('Speichern schliesst den Editor', await until(p, () => document.querySelector('#pg-modal').hidden));
    let sch = (await p.api('GET', '/api/notes/' + D + '/rows?limit=1')).schema;
    const fp = sch.props.find((x) => x.type === 'formula');
    ok('Schema: Ausdruck mit IDs, rtype number', fp && fp.expr === 'prop("preis") * prop("menge")' && fp.rtype === 'number', fp);
    ok('Tabelle zeigt die Werte (Zeile 3 -> 7,5)', await until(p, () => [...document.querySelectorAll('tr.db-row')].some((tr) =>
      /Zeile 3/.test(tr.querySelector('.db-title-in').value) && /7,5/.test(tr.textContent))));

    // Editor erneut: Namen statt IDs
    await p.api('PATCH', '/api/notes/' + D, { schema: sch });
    await p.open(D);
    const heads = await p.$$('th');
    for (const h of heads) { if (/ƒ/.test(await h.textContent())) { await h.click(); break; } }
    const edit = await p.waitForSelector('.menu:not([hidden]) .menu-item:has-text("Formel bearbeiten")', { timeout: 5000 }).catch(() => null);
    ok('Spaltenkopf: „Formel bearbeiten …“', !!edit);
    if (edit) await edit.click();
    ok('Editor zeigt die Formel mit Namen', await until(p, () => (document.querySelector('.fx-in') || {}).value === 'prop("Preis") * prop("Menge")'),
      await p.evaluate(() => (document.querySelector('.fx-in') || {}).value));
    await p.keyboard.press('Escape'); await p.click('#pg-modal-close').catch(() => {});

    // Fehler je Zelle + Wochentag + Gruppen nach Formel
    sch = (await p.api('GET', '/api/notes/' + D + '/rows?limit=1')).schema;
    sch.props.push({ id: 'inv', name: 'Kehrwert', type: 'formula', expr: '1 / prop("menge")' },
      { id: 'tag', name: 'Tag', type: 'formula', expr: 'formatDate(prop("d"), "dddd")' });
    sch.views = [{ id: 'tab', name: 'Tabelle', type: 'table' }, { id: 'brd', name: 'Board', type: 'board', group_by: 'tag' },
      { id: 'grp', name: 'Gruppiert', type: 'table', group: 'tag', sorts: [{ pid: fp.id, dir: 'desc' }], filters: [{ pid: fp.id, op: 'gt', value: '0' }] }];
    const sv = await p.api('PATCH', '/api/notes/' + D, { schema: sch });
    ok('Schema mit Board nach Formel gespeichert', sv.ok === true, sv);
    await view(p, D, 'tab'); await p.open(D);
    const err = await p.evaluate(() => { const e = document.querySelector('.fx-err'); return e ? [e.textContent, e.title] : null; });
    ok('#Fehler in der Zelle mit Erklaerung (Division durch 0)', err && err[0] === '#Fehler' && /Division/.test(err[1]), err);

    // sofortige Aktualisierung nach Bearbeiten: Menge in Zeile 0 aendern -> Formel-Zelle neu (PATCH calc)
    const ok0 = await p.evaluate(() => {
      const tr = [...document.querySelectorAll('tr.db-row')].find((x) => x.querySelector('.db-title-in').value === 'Zeile 0');
      const inp = tr && tr.querySelectorAll('td.db-cell input.val-in')[0];
      if (!inp) return false;
      inp.focus(); inp.value = '4'; inp.dispatchEvent(new Event('input')); inp.dispatchEvent(new Event('change')); return true;
    });
    ok('Zahl in Zeile 0 geaendert', ok0);
    ok('Formel-Zelle sofort neu (10, kein #Fehler mehr in der Zeile)', await until(p, () => {
      const tr = [...document.querySelectorAll('tr.db-row')].find((x) => x.querySelector('.db-title-in').value === 'Zeile 0');
      return tr && /10/.test(tr.textContent) && !tr.querySelector('.fx-err');
    }), await p.evaluate(() => { const tr = [...document.querySelectorAll('tr.db-row')].find((x) => x.querySelector('.db-title-in').value === 'Zeile 0'); return tr && tr.textContent; }));

    // Board nach Formel: Spalten = Wochentage Mo..So, nicht ziehbar
    await view(p, D, 'brd'); await p.open(D);
    const cols = await p.evaluate(() => [...document.querySelectorAll('.board-col')].map((c) => [c.querySelector('.board-head').textContent, c.querySelectorAll('.board-card').length]));
    ok('Board nach Formel: Spalten in fester Wochentags-Reihenfolge', JSON.stringify(cols.map((c) => c[0].replace(/\d+$/, ''))) ===
      JSON.stringify(['Ohne Tag', 'Montag', 'Dienstag', 'Mittwoch', 'Sonntag']), cols);
    ok('Board nach Formel: Spalten nehmen keine Karten an (nur lesend)', await p.evaluate(() => {
      const c = document.querySelector('.board-col'); return !c.hkDrop && /Formel/.test(c.title || '');
    }));
    // Paritaet: Server-Gruppen = Web-Spalten
    const srv = await p.api('GET', '/api/notes/' + D + '/rows?view=brd&tz=Europe/Berlin');
    ok('Paritaet Board: Gruppen vom Server = Spalten im Web', JSON.stringify(srv.groups.filter((g, i) => i > 0 || g.count).map((g) => [g.value === undefined ? 'Ohne Tag' : g.value, g.count])) ===
      JSON.stringify(cols.map((c) => [c[0].replace(/\d+$/, ''), c[1]])), { srv: srv.groups, cols });

    // Paritaet Tabelle gruppiert + Filter + Sortierung auf Formel
    await view(p, D, 'grp'); await p.open(D);
    const web = await p.evaluate(() => [...document.querySelectorAll('tr.db-row .db-title-in')].map((x) => x.value));
    const s2 = await p.api('GET', '/api/notes/' + D + '/rows?view=grp&tz=Europe/Berlin');
    ok('Paritaet Tabelle (gruppiert nach Formel, Filter/Sortierung Formel): Web = Server', JSON.stringify(web) === JSON.stringify(s2.rows.map((r) => r.title)) && web.length >= 3,
      { web, srv: s2.rows.map((r) => r.title) });
    const heads2 = await p.evaluate(() => [...document.querySelectorAll('tr.db-grow')].map((x) => x.textContent));
    ok('Gruppenkoepfe zeigen Formel-Werte', heads2.length >= 2 && heads2.some((h) => /Montag/.test(h)), heads2);
  } catch (e) {
    console.log('ABBRUCH', e && e.stack); res.push(false);
  } finally {
    if (p && D) await p.api('DELETE', '/api/notes/' + D).catch(() => {});
    await browser.close();
  }
  const n = res.filter(Boolean).length;
  console.log('\n' + n + '/' + res.length + ' ' + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
