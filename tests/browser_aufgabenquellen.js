// Aufgabenquellen (2.5, Paket 7 Teil A) im Web: Datenbank ueber das Menue "Ansicht" als Aufgabenquelle einrichten
// (Zuordnung aus den Typen vorgeschlagen), Block "Aufgaben" zeigt Inbox + Zeile mit Badge, Abhaken setzt den Status der
// Zeile, neue Aufgabe mit Ziel = Datenbank, Schaltflaeche "Saga-Aufgabe" mit Ziel, Dashboard-Liste, einmaliger Hinweis
// bei alter Vorlagen-Instanz, keine Seiten-/CSP-Fehler.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8290 CK=<Cookie> node tests/browser_aufgabenquellen.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(150); } }
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE', timezoneId: 'Europe/Berlin' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused/.test(m.text())) errs.push(m.text()); });
  await p.goto(BASE + '/notes');
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const row = async (id) => (await api('GET', '/api/notes/' + id));

  // --- Datenbank mit Status + Datum, ueber das Menue als Quelle einrichten ----------------------------------------------
  const db = (await api('POST', '/api/notes', { title: 'BQ Projekt', kind: 'database' })).id;
  await api('PATCH', '/api/notes/' + db, { schema: { props: [
    { id: 'st', name: 'Status', type: 'select', status: true, options: [{ id: 'neu', name: 'Neu', group: 'todo' }, { id: 'lauf', name: 'Läuft', group: 'doing' }, { id: 'ok', name: 'Fertig', group: 'done' }] },
    { id: 'fl', name: 'Fällig', type: 'date' }], views: [{ id: 'tab', name: 'Tabelle', type: 'table' }] } });
  const r1 = (await api('POST', '/api/notes', { title: 'BQ Angebot schreiben', parent_id: db, props: { st: 'neu', fl: '2020-01-02' } })).id;
  await api('POST', '/api/notes', { title: 'BQ Schon erledigt', parent_id: db, props: { st: 'ok' } });
  await api('POST', '/api/tasks', { text: 'BQ Inbox-Aufgabe' });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + db);
  await p.waitForSelector('.db-gear'); await sleep(600);
  await p.click('.db-gear');
  await p.waitForSelector('.vs-tasksrc');
  ok('Menue "Ansicht": "Als Aufgabenquelle verwenden …"', /Als Aufgabenquelle verwenden/.test(await p.locator('.vs-tasksrc').textContent()));
  await p.click('.vs-tasksrc');
  await p.waitForSelector('#pg-modal:not([hidden]) .tsrc-dlg');
  const sel = await p.evaluate(() => [...document.querySelectorAll('.tsrc-dlg select')].map((s) => s.value));
  ok(`Dialog: Vorschlag Status + Faellig (${sel.join(',')})`, sel[0] === 'st' && sel[1] === 'fl', sel);
  await p.click('#pg-modal .dlg-actions .btn-primary');
  const info = await until(async () => { const d = await api('GET', '/api/notes/' + db + '/tasksource'); return d.map && d.scope ? d : null; });
  ok('Gespeichert: Zuordnung im Schema + Abo "all"', info && info.map.done === 'st' && info.map.due === 'fl' && info.scope === 'all', info);

  // --- Seite mit Block "Aufgaben" und Schaltflaeche mit Ziel -----------------------------------------------------------
  const pg = (await api('POST', '/api/notes', { title: 'BQ Seite' })).id;
  await api('PATCH', '/api/notes/' + pg, { body: JSON.stringify({ blocks: [{ id: 'bq1', type: 'tasks', data: {} },
    { id: 'bq2', type: 'buttons', data: { buttons: [{ label: 'BQ in Projekt', icon: '☑️', action: 'task', target: db }] } }] }) });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + pg);
  await p.waitForSelector('.etk-list'); await sleep(500);
  const txt = await until(async () => { const t = await p.locator('.etk-list').textContent(); return /BQ Angebot/.test(t) && /BQ Inbox/.test(t) ? t : null; });
  ok('Block: Inbox + offene Zeile, erledigte fehlt', !!txt && !/BQ Schon erledigt/.test(txt), txt);
  const first = await p.locator('.etk-row .etk-text').first().textContent();
  ok(`Block: ueberfaellige Zeile oben (${first})`, first === 'BQ Angebot schreiben');
  const bdg = await p.locator('.etk-row.is-src .tsrc-badge').first().textContent();
  ok(`Block: Badge mit Datenbank-Namen (${bdg})`, /BQ Projekt/.test(bdg), bdg);
  const tg = p.locator('.etk-addbar .tsrc-target');
  ok('Ziel-Auswahl sichtbar (Inbox + Quelle)', await tg.isVisible() && (await tg.locator('option').count()) === 2);
  await tg.selectOption(String(db));
  await p.fill('.etk-add', 'BQ Neu im Projekt'); await p.press('.etk-add', 'Enter');
  const made = await until(async () => { const d = await api('GET', '/api/notes/' + db + '/rows'); return (d.rows || []).find((r) => r.title === 'BQ Neu im Projekt'); });
  ok('Neue Aufgabe mit Ziel: Zeile in der Datenbank, Status "neu"', made && made.props.st === 'neu', made);
  await p.locator('.etk-row.is-src', { hasText: 'BQ Angebot schreiben' }).locator('.etk-check').click();
  const st = await until(async () => { const r = await row(r1); return r.props && r.props.st === 'ok' ? r : null; });
  ok('Abhaken im Block: Status der Zeile = "Fertig"', !!st, st && st.props);
  ok('Toast mit "Rückgängig"', await until(() => p.locator('.toast:not([hidden])', { hasText: 'Rückgängig' }).count()));
  await p.locator('.nbtn', { hasText: 'BQ in Projekt' }).click();
  await p.waitForSelector('#pg-modal:not([hidden]) .task-quick input');
  await p.fill('.task-quick input', 'BQ per Knopf'); await p.press('.task-quick input', 'Enter');
  const kn = await until(async () => { const d = await api('GET', '/api/notes/' + db + '/rows'); return (d.rows || []).find((r) => r.title === 'BQ per Knopf'); });
  ok('Schaltflaeche "Saga-Aufgabe" mit Ziel: Zeile in der Datenbank', !!kn);
  const inbox = await api('GET', '/api/tasks');
  ok('... und keine Inbox-Aufgabe', !(inbox.open || []).some((t) => t.text === 'BQ per Knopf'));

  // --- Dashboard ------------------------------------------------------------------------------------------------------
  await p.goto(BASE + '/?t=' + Date.now());
  const dl = await until(async () => { const n = await p.locator('#task-list').count(); if (!n) return null; const t = await p.locator('#task-list').textContent(); return /BQ per Knopf/.test(t) ? t : null; }, 8000);
  ok('Dashboard: Zeilen der Quelle in der Aufgabenliste', !!dl && /BQ Inbox/.test(dl) && !/BQ Angebot/.test(dl), dl);

  // --- Hinweis bei alter Vorlagen-Instanz -----------------------------------------------------------------------------
  const old = (await api('POST', '/api/notes', { title: 'BQ Alte Aufgaben', kind: 'database' })).id;
  await api('PATCH', '/api/notes/' + old, { schema: { props: [
    { id: 'status', name: 'Status', type: 'select', options: [{ id: 'offen', name: 'Offen' }, { id: 'erledigt', name: 'Erledigt' }] },
    { id: 'faellig', name: 'Fällig', type: 'date' }], views: [{ id: 'a', name: 'A', type: 'table' }] } });
  await api('POST', '/api/notes', { title: 'BQ alt 1', parent_id: old, props: { status: 'offen' } });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + old);
  const hint = await until(() => p.locator('.toast:not([hidden])', { hasText: 'sieht nach Aufgaben aus' }).count(), 6000);
  ok('Alte Instanz: einmaliger Hinweis "sieht nach Aufgaben aus"', !!hint);
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + db);
  await p.waitForSelector('.db-gear'); await sleep(1500);
  ok('Neue Datenbank mit Status: kein Hinweis', !(await p.locator('.toast:not([hidden])', { hasText: 'sieht nach Aufgaben aus' }).count()));

  ok('Keine Seitenfehler / CSP-Verstoesse', !errs.length, errs);
  for (const x of [pg, db, old]) await api('DELETE', '/api/notes/' + x);
  const tasks = await api('GET', '/api/tasks');
  for (const t of (tasks.open || []).concat(tasks.done || [])) if (t && /^BQ /.test(t.text || '')) await api('DELETE', '/api/tasks/' + t.id);
  await br.close();
  const g = res.filter(Boolean).length;
  console.log('\nBROWSER-AUFGABENQUELLEN ' + g + '/' + res.length + (g === res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(g === res.length ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
