// 2.5 Fix-Runde 27.10. (Server-Teil im Web): E2 Seitenmenue "Als Startseite festlegen"/"zuruecksetzen", "/" oeffnet die
// Startseite, /dashboard bleibt; E4 "+ Neu ▾" aus Vorlage -> Peek mit Vorlagenname als Platzhalter, ohne Aenderung
// schliessen -> Eintrag endgueltig weg, mit Titel -> bleibt; Schaltflaeche mit Vorlage ebenso; #7 "+ Neu in …" ueber eine
// Relation nutzt die Standardvorlage der Ziel-Datenbank; E3 Widget-Quelle "Saga-Aufgaben"; #3 Kennzahl nach "+ Neu" sofort.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie eines Kontos> node tests/fix27_srv.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(150); } }
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource.*(404|403|409)/.test(m.text())) errs.push(m.text()); });
  await p.goto(BASE + '/notes');
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    const d = await r.json().catch(() => ({})); d._s = r.status; return d;
  }, [m, url, b]);
  const menu = async (label) => { const it = p.locator('.menu .menu-item', { hasText: label }).first(); await it.waitFor({ timeout: 4000 }); await it.click(); };
  const rows = async (db) => ((await api('GET', '/api/notes/' + db + '/rows')).rows || []);
  const open = async (id) => { await p.goto(BASE + '/notes#' + id); await p.waitForTimeout(300); await p.evaluate((i) => { location.hash = '#' + i; }, id); await until(() => p.evaluate((i) => !!document.querySelector('#pg-title') && window.location.hash === '#' + i, id)); await p.waitForTimeout(900); };
  try {
    // --- E2 -----------------------------------------------------------------------------------------------------
    const pg = (await api('POST', '/api/notes', { title: 'Fix27 Start' })).id;
    await open(pg);
    await p.click('#pg-more'); await menu('Als Startseite festlegen');
    ok('E2: Startseite gesetzt (API)', await until(async () => (await api('GET', '/api/account')).start_page === pg));
    await p.goto(BASE + '/');
    ok('E2: "/" oeffnet die Startseite', await until(() => p.evaluate((i) => location.pathname === '/notes' && location.hash === '#' + i, pg)), p.url());
    await until(() => p.evaluate(() => !!document.querySelector('.np-dash')));
    ok('E2: Dashboard-Knopf zeigt auf /dashboard', await p.evaluate(() => document.querySelector('.np-dash').getAttribute('href') === '/dashboard'));
    await p.goto(BASE + '/dashboard');
    ok('E2: /dashboard zeigt das Dashboard', p.url().endsWith('/dashboard') && (await p.title()).length > 0);
    await open(pg);
    await p.click('#pg-more'); await menu('Startseite zurücksetzen');
    ok('E2: Startseite zurueckgesetzt', await until(async () => (await api('GET', '/api/account')).start_page === null));

    // --- E4 + #3 --------------------------------------------------------------------------------------------------
    const db = (await api('POST', '/api/notes', { kind: 'database', title: 'Fix27 Buecher' })).id;
    await api('PATCH', '/api/notes/' + db, { schema: { props: [{ id: 'st', name: 'Status', type: 'select', options: [{ id: 'neu', name: 'Neu', color: 'blue' }] }],
      views: [{ id: 'v', name: 'Tabelle', type: 'table', open: 'side' }] } });
    const tpl = (await api('POST', '/api/notes', { parent_id: db, title: 'Neues Buch', props: { st: 'neu' } })).id;
    await api('PATCH', '/api/notes/' + tpl, { template: true, format: 'blocks', body: JSON.stringify({ blocks: [{ type: 'header', data: { text: 'Worum geht es', level: 3 } }, { type: 'paragraph', data: { text: '' } }] }) });
    await api('PATCH', '/api/notes/' + db, { schema: { default_tpl: tpl, props: [{ id: 'st', name: 'Status', type: 'select', options: [{ id: 'neu', name: 'Neu', color: 'blue' }] }],
      views: [{ id: 'v', name: 'Tabelle', type: 'table', open: 'side' }] } });
    await open(db);
    const nb = p.locator('.db-new-top').first(); await nb.waitFor();
    ok('E1/E4: "+ Neu ▾" mit Vorlagen', /▾/.test(await nb.textContent()));
    await nb.click(); await menu('Neues Buch');
    const frame = () => p.frame({ url: /embed=1/ });
    ok('E4: Peek offen', await until(() => p.evaluate(() => !document.getElementById('pg-peek').hidden)));
    const ph = await until(async () => { const f = frame(); if (!f) return null; return f.evaluate(() => { const t = document.getElementById('pg-title'); return t && document.getElementById('page') && !document.getElementById('page').hidden ? t.placeholder + '|' + t.value : null; }).catch(() => null); }, 8000);
    ok(`E4: leerer Titel, Vorlagenname als Platzhalter (${ph})`, ph === 'Neues Buch|');
    await p.waitForTimeout(1500);                       // Editor gezeichnet (darf nichts "aendern")
    let r = await rows(db);
    ok('E4: Eintrag angelegt', r.length === 1, r);
    await p.click('#peek-close');
    ok('E4: ohne Aenderung geschlossen -> endgueltig weg', await until(async () => (await rows(db)).length === 0, 6000), await rows(db));
    const trash = await api('GET', '/api/trash');
    ok('E4: nicht im Papierkorb', Array.isArray(trash) && !trash.some((x) => x.parent_id === db));
    await nb.click(); await menu('Neues Buch');
    await until(async () => { const f = frame(); return f && f.evaluate(() => !document.getElementById('page').hidden && document.activeElement && true).catch(() => false); }, 8000);
    await p.waitForTimeout(1200);
    const f = frame();
    await f.fill('#pg-title', 'Dune');
    await p.waitForTimeout(300);
    await p.click('#peek-close');
    await p.waitForTimeout(1500);
    r = await rows(db);
    ok('E4: mit Titel geschlossen -> bleibt', r.length === 1 && r[0].title === 'Dune', r);

    // Schaltflaeche mit Vorlage, oeffnet als ganze Seite; Wechsel zu einer anderen Seite verwirft
    const host = (await api('POST', '/api/notes', { title: 'Fix27 Knopf' })).id;
    await api('PATCH', '/api/notes/' + host, { format: 'blocks', body: JSON.stringify({ blocks: [
      { type: 'buttons', data: { buttons: [{ label: 'Buch hinzu', action: 'db', target: db, tpl: tpl, title: '' }] } },
      { id: 'wk1', type: 'widget', data: { v: 1, kind: 'metric', title: 'Buecher', cfg: { db: db, view: '', agg: 'count', prop: '', fmt: 'num' } } }] }) });
    await open(host);
    const wv = () => p.evaluate(() => { const w = document.querySelector('.wg-v'); return w ? w.textContent.trim() : ''; });
    ok(`#3: Kennzahl zeigt 1 (${await until(wv)})`, (await until(async () => (await wv()) === '1')));
    await p.locator('button', { hasText: 'Buch hinzu' }).first().click();
    const nid = await until(async () => { const rr = await rows(db); return rr.length === 2 ? rr.find((x) => x.title === '').id : null; });
    ok('E4: Schaltflaeche legt an und oeffnet die Seite', !!nid && await until(() => p.evaluate((i) => location.hash === '#' + i, nid)));
    ok('E4: Platzhalter auf der ganzen Seite', await until(() => p.evaluate(() => document.getElementById('pg-title').placeholder === 'Neues Buch')));
    await p.waitForTimeout(1200);
    await open(host);
    ok('E4: Seite gewechselt -> neuer Eintrag weg', await until(async () => (await rows(db)).length === 1, 6000));
    ok(`#3: Kennzahl nach dem Verwerfen wieder 1 (${await wv()})`, await until(async () => (await wv()) === '1', 4000));

    // #7: Relation "+ Neu in …" nutzt die Standardvorlage der Ziel-Datenbank
    const pdb = (await api('POST', '/api/notes', { kind: 'database', title: 'Fix27 Regale' })).id;
    await api('PATCH', '/api/notes/' + pdb, { schema: { props: [{ id: 'bu', name: 'Buecher', type: 'relation', target: db }], views: [{ id: 't', name: 'T', type: 'table' }] } });
    await api('POST', '/api/notes', { parent_id: pdb, title: 'Regal 1' });
    await open(pdb);
    const rel = p.locator('.val-relation .val-btn').first(); await rel.waitFor();
    await rel.click();
    await p.locator('.menu .menu-input').first().fill('Neuromancer');
    await menu('+ Neu in');
    const made = await until(async () => (await rows(db)).find((x) => x.title === 'Neuromancer'));
    ok('#7: neuer Eintrag ueber die Relation mit Werten der Standardvorlage', made && made.props.st === 'neu', made);
    const mb = made && await api('GET', '/api/notes/' + made.id);
    ok('#7: ... und mit Inhalt der Vorlage', mb && /Worum geht es/.test(mb.body || ''));

    // E3: Widget-Quelle "Saga-Aufgaben"
    const wp = (await api('POST', '/api/notes', { title: 'Fix27 Aufgaben' })).id;
    await api('PATCH', '/api/notes/' + wp, { format: 'blocks', body: JSON.stringify({ blocks: [
      { id: 'wt1', type: 'widget', data: { v: 1, kind: 'metric', title: 'Offen', cfg: { src: 'tasks', agg: 'open', fmt: 'num' } } }] }) });
    await api('POST', '/api/tasks', { text: 'Fix27 eins' }); await api('POST', '/api/tasks', { text: 'Fix27 zwei' });   // frisches Konto: 2 offen
    await open(wp);
    const shown = await until(async () => { const v = await wv(); return v === '2' ? v : null; });
    ok(`E3: Kennzahl aus Saga-Aufgaben (${await wv()})`, shown === '2');
    await p.locator('.wg-card, .widget, .wg').first().click().catch(() => {});
    const srcOk = await until(() => p.evaluate(() => [...document.querySelectorAll('.wg-panel select option')].some((o) => o.value === 'tasks' && o.selected)), 3000);
    ok('E3: Einstellungen zeigen Quelle "Saga-Aufgaben"', srcOk);
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e));
  }
  ok('Konsole ohne Fehler', !errs.length, errs.slice(0, 5));
  await br.close();
  console.log(`\nFIX27-WEB ${res.filter(Boolean).length}/${res.length} - ` + (res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
