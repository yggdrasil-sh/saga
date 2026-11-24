// Saga 2.6 (Bugs-DB 2031) - leere Zustaende mit naechstem Schritt und "Erste Schritte" (ohne KI vollstaendig):
// neues Konto -> Frage nach der Einfuehrung; Heute zeigt leere Zustaende (Weitermachen, Aufgaben, Termine mit
// "Kalender verbinden"); "Nein, danke" -> keine Frage mehr, nichts angelegt; Einstellungen > Hilfe legt die Seite an;
// leere Datenbank zeigt "+ Neuer Eintrag". Konto SAGA_FS_USER (Standard w0-fs) muss leer sein (browser.sh setzt es zurueck).
// SAGA_BASE, SAGA_FS_USER/SAGA_PASS, SHOTS=/ordner
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const E = process.env; const BASE = E.SAGA_BASE || 'http://127.0.0.1:8099'; const U = E.SAGA_FS_USER || 'w0-fs';
const R = []; const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x || ''))); };
const errs = [];
const api = (p, m, u, b) => p.evaluate(([m, u, b]) => fetch(u, { method: m, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Saga': '1' },
  body: b ? JSON.stringify(b) : undefined }).then(r => r.json().then(d => ({ s: r.status, d })).catch(() => ({ s: r.status, d: null }))), [m, u, b]);
const until = async (fn, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { const v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, 150)); } return null; };

(async () => {
  const br = await chromium.launch(E.PW_CHANNEL ? { channel: E.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1440, height: 900 }, locale: 'de-DE' });
  const p = await ctx.newPage(); p.on('pageerror', e => { errs.push(e.message); console.log('[err]', e.message); });
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', U); await p.fill('input[name=password]', E.SAGA_PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  await p.goto(BASE + '/dashboard');

  // --- Frage beim ersten Anmelden ---
  const q = await until(() => p.evaluate(() => { const h = document.querySelector('.hk-ask .hk-ask-t'); return h && h.textContent; }), 8000);
  ok('neues Konto: Frage nach der Einführung', /kurze Einführung/.test(q || ''), q);
  const btns = await p.evaluate(() => [].map.call(document.querySelectorAll('.hk-ask .hk-ask-bar .btn'), b => b.textContent));
  ok('Knöpfe sprechen für mich: „Nein, danke“ und „Ja, gern“', btns.join('|') === 'Nein, danke|Ja, gern', btns);
  if (E.SHOTS) await p.screenshot({ path: E.SHOTS + '/erste-schritte-frage.png' });

  // --- leere Zustaende auf Heute (hinter dem Dialog schon gezeichnet) ---
  const st = await p.evaluate(() => {
    const t = id => { const n = document.getElementById(id); return n ? { hidden: n.hidden, text: n.textContent, go: !!n.querySelector('.hk-empty-go'), goT: (n.querySelector('.hk-empty-go') || {}).textContent } : null; };
    return { recent: t('hz-recent-empty'), tasks: t('task-empty'), rail: t('rail-empty'), cal: t('rail-cal'), calAdd: !document.getElementById('rail-cal-add') || document.getElementById('rail-cal-add').hidden };
  });
  ok('Weitermachen: Satz + „Neue Seite“', st.recent && !st.recent.hidden && /zuletzt geöffnet/.test(st.recent.text) && st.recent.goT === 'Neue Seite', st.recent);
  ok('Aufgaben: was hier erscheint + „Aufgabe notieren“', st.tasks && !st.tasks.hidden && /Fälligkeit/.test(st.tasks.text) && st.tasks.goT === 'Aufgabe notieren', st.tasks);
  ok('Aufgabenleiste: Satz ohne zweiten Knopf (Eingabe steht darüber)', st.rail && !st.rail.hidden && /deine Aufgaben/.test(st.rail.text) && !st.rail.go, st.rail);
  ok('Termine ohne Kalender: Hinweis + „Kalender verbinden“, kein „+“', st.cal && !st.cal.hidden && /Kalender verbinden/.test(st.cal.text) && st.calAdd, st.cal);

  // --- Pruefer 3: Escape/Wegklicken speichert nichts, beim naechsten Laden kommt die Frage wieder ---
  await p.keyboard.press('Escape'); await p.waitForTimeout(600);
  let fs0 = await api(p, 'GET', '/api/first-steps');
  ok('Escape: nichts gespeichert, weiter „fragen“', fs0.d.state === null && fs0.d.ask === true, fs0.d);
  await p.reload();
  const q2 = await until(() => p.evaluate(() => { const h = document.querySelector('.hk-ask .hk-ask-t'); return h && h.textContent; }), 8000);
  ok('nach dem Neuladen: Frage kommt wieder', /kurze Einführung/.test(q2 || ''), q2);

  // --- Nein: nichts angelegt, keine Frage mehr ---
  await p.click('.hk-ask .hk-ask-bar .btn:not(.btn-primary)');
  await p.waitForTimeout(800);
  let fs = await api(p, 'GET', '/api/first-steps');
  const own = await api(p, 'GET', '/api/notes');
  const n0 = (Array.isArray(own.d) ? own.d : (own.d && own.d.notes) || []).length;
  ok('„Nein, danke“: Zustand no, keine Seite', fs.d.state === 'no' && !fs.d.ask && !fs.d.page && n0 === 0, { fs: fs.d, n0 });
  await p.reload(); await p.waitForTimeout(1800);
  ok('nach dem Neuladen: keine Frage mehr', !(await p.$('.hk-ask')));
  // Kalender-Knopf fuehrt in die Einstellungen
  await p.click('#rail-cal .hk-empty-go');
  ok('„Kalender verbinden“ öffnet Einstellungen › Kalender', !!(await until(() => p.evaluate(() => { const x = document.getElementById('hk-pane-calendar'); return x && !x.hidden && x.getClientRects().length > 0; }), 4000)));
  await p.keyboard.press('Escape'); await p.waitForTimeout(300);

  // --- Einstellungen > Hilfe: Erste Schritte anlegen ---
  await p.evaluate(() => HKSettings.open('help'));
  const fsb = await until(() => p.$('.hk-fs-open'), 5000);
  ok('Einstellungen › Hilfe: „Erste Schritte anlegen“', fsb && (await fsb.textContent()) === 'Erste Schritte anlegen');
  if (E.SHOTS) await p.screenshot({ path: E.SHOTS + '/erste-schritte-hilfe.png' });
  await Promise.all([p.waitForNavigation(), fsb.click()]);
  await p.waitForTimeout(2000);
  fs = await api(p, 'GET', '/api/first-steps');
  const title = await p.evaluate(() => { const t = document.querySelector('#page .pg-title, #pg-title, [contenteditable].pg-title'); return t ? (t.value || t.textContent) : document.title; });
  ok('Seite „Erste Schritte“ ist offen', /Erste Schritte/.test(title) && fs.d.state === 'yes' && fs.d.page, { title, fs: fs.d });
  const pg = await api(p, 'GET', '/api/notes/' + fs.d.page);
  const items = (JSON.parse(pg.d.body || '{}').blocks || []).filter(b => b.type === 'list').flatMap(b => b.data.items || []);
  ok('Häkchenliste mit 5 Punkten', items.length === 5, items.length);
  if (E.SHOTS) await p.screenshot({ path: E.SHOTS + '/erste-schritte-seite.png' });

  // --- leere Datenbank: "+ Neuer Eintrag" ---
  const db = await api(p, 'POST', '/api/notes', { title: 'Leere Liste', kind: 'database' });
  await p.goto(BASE + '/notes#' + db.d.id); await p.waitForTimeout(2200);
  const de = await p.evaluate(() => { const r = document.querySelector('.db-empty-row'); return r ? { text: r.textContent, go: !!r.querySelector('.hk-empty-go') } : null; });
  ok('leere Datenbank: Satz + „+ Neuer Eintrag“', de && /Noch keine Einträge/.test(de.text) && de.go, de);
  if (de && de.go) {
    await p.click('.db-empty-row .hk-empty-go'); await p.waitForTimeout(1500);
    const rows = await api(p, 'GET', '/api/notes/' + db.d.id + '/rows');
    const nr = Array.isArray(rows.d) ? rows.d.length : ((rows.d && rows.d.rows) || []).length;
    ok('„+ Neuer Eintrag“ legt einen Eintrag an, der Hinweis verschwindet', nr === 1 && !(await p.$('.db-empty-row')), { nr });
  }
  // nach eigener Seite + Aufgabe: Saga hakt ab
  await api(p, 'POST', '/api/tasks', { text: 'Erste Aufgabe' });
  fs = await api(p, 'GET', '/api/first-steps');
  const pg2 = await api(p, 'GET', '/api/notes/' + fs.d.page);
  const it2 = (JSON.parse(pg2.d.body || '{}').blocks || []).filter(b => b.type === 'list').flatMap(b => b.data.items || []);
  ok('eigene Datenbank und Aufgabe: Saga hakt die zwei Punkte ab', it2.filter(x => x.meta && x.meta.checked).length >= 2, it2.map(x => !!(x.meta && x.meta.checked)));

  ok('keine Skriptfehler', errs.length === 0, errs);
  await br.close();
  const bad = R.filter(x => !x[1]);
  console.log(bad.length ? `\n${R.length - bad.length}/${R.length} - FEHLER` : `\nLeere Zustände ${R.length}/${R.length} - ALLES GRUEN`);
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
