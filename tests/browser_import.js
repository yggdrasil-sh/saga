// Browser-Test 2.5 (Paket 2): Import-Dialog (CSV -> neue Datenbank: Vorschau, Typ aendern, Spalte weglassen, Fortschritt,
// Bericht, Oeffnen), "Zeilen aus CSV hinzufuegen" an einer Datenbank, CSV-Export aus dem Ansicht-Menue,
// optional Markdown-Ordner (IMPORT_MD=1) und Notion-Export (IMPORT_NOTION=1: ZIP aus tests/fixtures/notion/a, Typ einer
// Spalte im Dialog aendern, Formel-Spalte "In Formel umwandeln" im Spaltenmenue).
//   SAGA_BASE=... SAGA_USER=... SAGA_PASS=... [PLAYWRIGHT=...] node tests/browser_import.js
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function until(p, fn, arg, ms = 20000) {
  for (const t0 = Date.now(); Date.now() - t0 < ms; await p.waitForTimeout(150)) if (await p.evaluate(fn, arg)) return true;
  return false;
}
(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'imp-'));
  const csv = path.join(tmp, 'Bestellungen.csv');
  fs.writeFileSync(csv, 'Artikel;Menge;Preis;Geliefert;Kategorie;Unwichtig\nSchrauben;100;1,25;ja;Kleinteile;x\nMuttern;50;0,80;nein;Kleinteile;y\nBohrer;2;19,90;ja;Werkzeug;z\n');
  const csv2 = path.join(tmp, 'mehr.csv');
  fs.writeFileSync(csv2, 'Artikel,Menge\nDübel,30\n');
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1366, height: 900 }, locale: 'de-DE', acceptDownloads: true });
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
    const home = (await api('POST', '/api/notes', { title: 'Importtest Start' })).id; made.push(home);
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + home);
    await p.waitForSelector('#pg-new');

    // --- Neue Seite -> Importieren ---------------------------------------------------------------------------------
    await p.click('#pg-new');
    await p.waitForSelector('.menu:not([hidden]) .menu-item');
    const items = await p.$$eval('.menu:not([hidden]) .menu-item', (xs) => xs.map((x) => x.textContent));
    ok('Menue "+ Neue Seite" hat "Importieren …"', items.some((x) => x.includes('Importieren')), items);
    await p.click('.menu:not([hidden]) .menu-item:has-text("Importieren")');
    await p.waitForSelector('#pg-modal:not([hidden]) .imp-drop');
    ok('Dialog: Ablagefeld, Datei- und Ordner-Knopf', await p.$('.imp-file') && await p.$('.imp-dir'));
    await p.setInputFiles('.imp-file', csv);
    await p.waitForSelector('.imp-tbl', { timeout: 20000 });
    const heads = await p.$$eval('.imp-tbl th .imp-cname', (xs) => xs.map((x) => x.textContent));
    ok('Vorschau: Spalten aus der Kopfzeile', heads.join('|') === 'Artikel|Menge|Preis|Geliefert|Kategorie|Unwichtig', heads);
    const rows = await p.$$eval('.imp-tbl tbody tr', (xs) => xs.length);
    ok('Vorschau: 3 Zeilen', rows === 3, rows);
    const types = await p.$$eval('.imp-tbl th select.imp-type', (xs) => xs.map((x) => x.value));
    ok('Typen erkannt (Zahl, Haekchen, Auswahl)', types[1] === 'number' && types[2] === 'number' && types[3] === 'checkbox' && types[4] === 'select', types);
    ok('Titelspalte: erste Spalte, ihr Typ gesperrt', await p.$eval('.imp-tbl th input[type=radio]', (x) => x.checked)
       && await p.$eval('.imp-tbl th select.imp-type', (x) => x.disabled));
    const sels = await p.$$('.imp-tbl th select.imp-type');
    await sels[5].selectOption('skip');
    await sels[4].selectOption('text');
    ok('Name vorbelegt', await p.$eval('.imp-title', (x) => x.value) === 'Bestellungen');
    await p.fill('.imp-title', 'Bestellungen Import');
    await p.click('.imp-run');
    ok('Fortschritt, dann fertig', await until(p, () => !!document.querySelector('.imp-done.ok')), await p.$eval('#pg-modal-body', (x) => x.textContent.slice(0, 200)));
    const sum = await p.$eval('.imp-sum', (x) => x.textContent);
    ok('Zusammenfassung: 1 Datenbank, 3 Eintraege', /1 Datenbanken/.test(sum) && /3 Einträge/.test(sum), sum);
    await p.click('.imp-open');
    await p.waitForSelector('.db-bar');
    await p.waitForTimeout(500);
    ok('Datenbank geoeffnet, Titel', (await p.$eval('#pg-title', (x) => x.value || x.textContent)).includes('Bestellungen Import'));
    const dbId = await p.evaluate(() => parseInt(location.hash.slice(1), 10)); made.push(dbId);
    const d = await api('GET', '/api/notes/' + dbId + '/rows');
    const names = d.schema.props.map((x) => x.name + ':' + x.type);
    ok('Schema: Typ geaendert, Spalte weggelassen', names.join(',') === 'Menge:number,Preis:number,Geliefert:checkbox,Kategorie:text', names);
    const sch = d.rows.find((r) => r.title === 'Schrauben');
    ok('Werte (Dezimalkomma, Haekchen)', sch && Object.values(sch.props).includes(1.25) && Object.values(sch.props).includes(true), sch);
    ok('"+ Neue Seite": Datenbank auf oberster Ebene', (await api('GET', '/api/notes/' + dbId)).parent_id == null);

    // --- Zeilen aus CSV hinzufuegen ------------------------------------------------------------------------------------
    await p.click('.db-gear');
    await p.waitForSelector('.vs-csv-in');
    await p.click('.vs-csv-in');
    await p.waitForSelector('#pg-modal:not([hidden]) .imp-drop');
    await p.setInputFiles('.imp-file', csv2);
    await p.waitForSelector('.imp-tbl select.imp-target');
    const tg = await p.$$eval('.imp-tbl select.imp-target', (xs) => xs.map((x) => x.value));
    ok('Zuordnung vorbelegt (Titel, gleichnamige Spalte)', tg[0] === 'title' && tg[1] !== '', tg);
    await p.click('.imp-run');
    ok('Anhaengen fertig', await until(p, () => !!document.querySelector('.imp-done.ok')));
    await p.click('.imp-open');
    await p.waitForTimeout(800);
    const d2 = await api('GET', '/api/notes/' + dbId + '/rows');
    ok('Zeile angehaengt', d2.rows.length === 4 && d2.rows.some((r) => r.title === 'Dübel'), d2.rows.map((r) => r.title));

    // --- CSV-Export aus dem Ansicht-Menue -------------------------------------------------------------------------------
    await p.click('.db-gear');
    await p.waitForSelector('.vs-csv');
    const [dl] = await Promise.all([p.waitForEvent('download'), p.click('.vs-csv')]);
    const file = path.join(tmp, 'x.csv'); await dl.saveAs(file);
    const txt = fs.readFileSync(file, 'utf8');
    ok('CSV-Export: Datei mit BOM, Kopf und Zeilen', dl.suggestedFilename().endsWith('.csv') && txt.charCodeAt(0) === 0xfeff
       && txt.includes('Schrauben') && txt.split('\r\n')[0].includes('Menge'), txt.slice(0, 120));

    // --- Markdown-Ordner (optional) -----------------------------------------------------------------------------------
    if (process.env.IMPORT_MD === '1') {
      const dir = path.join(tmp, 'Tresor');
      fs.mkdirSync(path.join(dir, 'Projekte'), { recursive: true });
      fs.writeFileSync(path.join(dir, 'Start.md'), '---\ntags: [privat]\n---\n# Start\n\nSiehe [[Alpha]] und **fett**.\n');
      fs.writeFileSync(path.join(dir, 'Projekte', 'Alpha.md'), '- [ ] offen\n- [x] erledigt\n');
      await p.click('#pg-new');
      await p.click('.menu:not([hidden]) .menu-item:has-text("Importieren")');
      await p.waitForSelector('#pg-modal:not([hidden]) .imp-drop');
      await p.setInputFiles('.imp-dir', dir);
      await p.waitForSelector('.imp-run');
      const ms = await p.$eval('.imp-sum', (x) => x.textContent);
      ok('Markdown-Analyse: 3 Seiten (2 Notizen + Ordner), 1 Ordner', /3 Seiten, 1 Ordner/.test(ms), ms);
      await p.click('.imp-run');
      ok('Markdown fertig', await until(p, () => !!document.querySelector('.imp-done.ok')));
      await p.click('.imp-open');
      await p.waitForTimeout(800);
      const root = await p.evaluate(() => parseInt(location.hash.slice(1), 10)); made.push(root);
      ok('Sammelseite geoeffnet', root > 0);
    }
    // --- Notion-Export (optional) ---------------------------------------------------------------------------------------
    if (process.env.IMPORT_NOTION === '1') {
      const zip = path.join(tmp, 'Export-test.zip');
      require('child_process').execFileSync('zip', ['-qr', zip, '.'], { cwd: path.join(__dirname, 'fixtures', 'notion', 'a') });
      await p.click('#pg-new');
      await p.click('.menu:not([hidden]) .menu-item:has-text("Importieren")');
      await p.waitForSelector('#pg-modal:not([hidden]) .imp-drop');
      ok('Hinweis nennt Notion', (await p.$eval('.imp-hint', (x) => x.textContent)).includes('Notion'));
      await p.setInputFiles('.imp-file', zip);
      await p.waitForSelector('.imp-ndb', { timeout: 20000 });
      const ns = await p.$eval('.imp-sum', (x) => x.textContent);
      ok('Notion-Analyse: 2 Datenbanken, 7 Eintraege', /2 Datenbanken/.test(ns) && /7 Einträge/.test(ns), ns);
      const dbs = await p.$$('.imp-ndb');
      await dbs[1].evaluate((d) => { d.open = true; });
      const nsel = await dbs[1].$$('select.imp-ntype');
      const nv = await Promise.all(nsel.map((x) => x.evaluate((e) => e.value)));
      ok('Typen vorgeschlagen (Status, Formel)', nv[0] === 'status' && nv.includes('fx') && nv.includes('relation'), nv);
      await nsel[nv.indexOf('select')].selectOption('skip');           // Kategorie weglassen
      await p.fill('.imp-title', 'Aus Notion');
      await p.click('.imp-run');
      ok('Notion-Import fertig', await until(p, () => !!document.querySelector('.imp-done.ok'), null, 60000),
        await p.$eval('#pg-modal-body', (x) => x.textContent.slice(0, 300)));
      ok('Bericht im Dialog (Personen/Formel)', (await p.$$eval('.imp-grp summary', (xs) => xs.map((x) => x.textContent).join('|'))).includes('Formel'));
      await p.click('.imp-open');
      await p.waitForTimeout(800);
      const nroot = await p.evaluate(() => parseInt(location.hash.slice(1), 10)); made.push(nroot);
      ok('Sammelseite "Aus Notion" geoeffnet', (await p.$eval('#pg-title', (x) => x.value || x.textContent)).includes('Aus Notion'));
      const all = await api('GET', '/api/notes');
      const list = all.notes || all.pages || all;
      const pj = (Array.isArray(list) ? list : []).find((x) => x.title === 'Projekte' && x.kind === 'database');
      ok('Datenbank Projekte angelegt', !!pj, Array.isArray(list) ? list.length : list);
      if (pj) {
        const dr = await api('GET', '/api/notes/' + pj.id + '/rows');
        ok('Kategorie weggelassen, Status-Eigenschaft', !dr.schema.props.some((x) => x.name === 'Kategorie')
           && dr.schema.props.some((x) => x.name === 'Status' && x.status), dr.schema.props.map((x) => x.name));
        await p.goto(BASE + '/notes?t=' + Date.now() + '#' + pj.id);
        await p.waitForSelector('.db-bar');
        await p.waitForTimeout(600);
        const th = await p.$$('th.db-th');
        let hit = null;
        for (const h of th) if ((await h.textContent()).includes('Summe (Formel)')) { hit = h; break; }
        ok('Spaltenkopf der Formel-Spalte', !!hit);
        if (hit) {
          await hit.scrollIntoViewIfNeeded();
          await hit.evaluate((e) => e.click());                         // breite Tabelle: Mitte des Kopfs evtl. unter dem Rand
          await p.waitForSelector('.menu:not([hidden]) .menu-item');
          const mi = await p.$$eval('.menu:not([hidden]) .menu-item', (xs) => xs.map((x) => x.textContent));
          ok('Spaltenmenue: "In Formel umwandeln …"', mi.some((x) => x.includes('In Formel umwandeln')), mi);
          await p.$$eval('.menu:not([hidden]) .menu-item', (xs) => xs.find((x) => x.textContent.includes('In Formel umwandeln')).click());
          await p.waitForSelector('.fx-in');
          await p.fill('.fx-in', 'prop("Budget") * 2');
          await p.waitForSelector('.fx-status.ok', { timeout: 10000 });
          p.once('dialog', (d) => d.accept());
          await p.click('.fx-save');
          await p.waitForTimeout(1200);
          const dr2 = await api('GET', '/api/notes/' + pj.id + '/rows');
          const fx = dr2.schema.props.find((x) => x.name === 'Summe (Formel)');
          const al = dr2.rows.find((r) => r.title === 'Projekt Alpha');
          ok('Umgewandelt: Formel, Wert gerechnet', fx && fx.type === 'formula' && !fx.fx_hint && al && al.props[fx.id] === 2469, fx);
        }
      }
    }
  } catch (e) {
    console.log('FEHLER', e.message); res.push(false);
  } finally {
    const api = (m, u) => p.evaluate(async ([m, u]) => { await fetch(u, { method: m, headers: { 'X-Saga': '1' } }); }, [m, u]);
    for (const id of made) await api('DELETE', '/api/notes/' + id).catch(() => {});
    await br.close();
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  console.log(`\nBROWSER-IMPORT ${res.filter(Boolean).length}/${res.length}`, res.length && res.every(Boolean) ? '- ALLES GRUEN' : '- FEHLER');
  process.exit(res.length && res.every(Boolean) ? 0 : 1);
})();
