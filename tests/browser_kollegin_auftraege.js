// Saga 2.6 (Bugs-DB 2267 K-B) - Ort „Aufträge“ im Web (static/ai-jobs.js): Liste in 4 Gruppen (Wartet auf dich · Läuft ·
// Fertig · Hat nicht geklappt), Detail mit genauem Zustandswort, Plan, „Was ich gelesen habe“ und Protokoll aus job_event,
// Rückgängig dauerhaft im Detail, bei Konflikt „Das wurde seitdem geändert.“ + „Alte Fassung ansehen“, Zustandswörter als
// Systemtext in allen 5 Sprachen, leerer Zustand, Tastatur (Liste per Tab erreichbar), hell/dunkel, keine Skriptfehler.
// Bilder (SHOTS=<ordner>): auftraege-hell/dunkel, detail-dunkel, konflikt-dunkel.
// Voraussetzung wie tests/browser_kollegin_plan.js (fake-plan, Stufe „Pläne“, Seite „Sommerfest“, frisches Konto).
//   SAGA_BASE=http://127.0.0.1:8900 SAGA_USER=kp-web2 SAGA_PASS=... [SHOTS=/tmp/x] node tests/browser_kollegin_auftraege.js
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const E = process.env, BASE = E.SAGA_BASE || 'http://127.0.0.1:8099', USER = E.SAGA_USER, PASS = E.SAGA_PASS, SHOTS = E.SHOTS || '';
if (!USER || !PASS) { console.error('SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const R = []; const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x === undefined ? '' : x))); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 8000)) return v; await sleep(150); } }
const WORDS = {
  de: ['Wartet auf dich', 'Läuft', 'Fertig', 'Hat nicht geklappt'], en: ['Needs you', 'In progress', 'Finished', "Didn't work"],
  es: ['Te necesita', 'En curso', 'Terminados', 'No ha funcionado'], ru: ['Нужен ты', 'Выполняется', 'Завершено', 'Не получилось'],
  th: ['ต้องการคุณ', 'กำลังทำ', 'เสร็จสิ้น', 'ไม่สำเร็จ'] };

(async () => {
  const br = await chromium.launch(E.PW_CHANNEL ? { channel: E.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1440, height: 900 }, locale: 'de-DE', colorScheme: 'dark' });
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    const d = await r.json().catch(() => ({})); return { s: r.status, d: d };
  }, [m, url, b]);
  const sse = (b) => p.evaluate(async (b) => {               // POST /api/ai/jobs -> letztes Ereignis {typ, data}
    const r = await fetch('/api/ai/jobs', { method: 'POST', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
    const t = await r.text(); let last = null;
    t.split('\n\n').forEach((ch) => { const ev = /event: (\w+)/.exec(ch), da = /data: (.*)/.exec(ch); if (ev) last = { typ: ev[1], data: da ? JSON.parse(da[1]) : null }; });
    return last;
  }, b);
  await api('PUT', '/api/account/lang', { lang: 'de' });
  await api('POST', '/api/first-steps', { answer: 'no' });
  async function openJobs() {
    await p.goto(BASE + '/dashboard'); await until(() => p.evaluate(() => !!(window.HKAI && HKAI.state && HKAI.state())));
    await p.evaluate(() => HKAI.open('jobs'));
    return until(() => p.evaluate(() => !!document.querySelector('.kj-root .kj-list') && !document.querySelector('.kj-list:empty')));
  }
  async function shot(name) { if (SHOTS) { const b = await p.$('#ai-panel'); if (b) await b.screenshot({ path: SHOTS + '/' + name + '.png' }); } }

  // leerer Zustand
  ok('Ort „Aufträge“ öffnet', await openJobs());
  const empty = await p.evaluate(() => (document.querySelector('.kj-empty') || {}).textContent || '');
  ok('leer: „Noch keine Aufträge. Gib mir einen, z. B. …“ + Beispiel-Chip', /^Noch keine Aufträge\. Gib mir einen/.test(empty) && /Pavillon/.test(empty), empty);
  await shot('auftraege-leer-dunkel');
  // Daten: wartet, erledigt, abgebrochen, erledigt mit spaeterer Aenderung (Konflikt)
  const wait = (await sse({ recipe: 'aufgabe', fields: { text: 'Pavillon abholen' } })).data;
  const done = (await sse({ recipe: 'aufgabe', fields: { text: 'Grillkohle kaufen' } })).data;
  await api('POST', '/api/ai/jobs/' + done.job + '/approve', { plan_v: done.plan_v, plan_hash: done.plan_hash });
  const stop = (await sse({ recipe: 'aufgabe', fields: { text: 'Servietten' } })).data;
  await api('POST', '/api/ai/jobs/' + stop.job + '/cancel', {});
  const notes = (await api('GET', '/api/notes')).d;
  const fest = (Array.isArray(notes) ? notes : notes.notes || []).find((n) => n.title === 'Sommerfest');
  const conf = (await sse({ text: 'Trag auf der Seite Sommerfest ein, dass Ben die Grillkohle mitbringt.' })).data;
  ok('Testdaten angelegt', wait && done && stop && conf && conf.job && fest, { wait, conf, fest: !!fest });
  await api('POST', '/api/ai/jobs/' + conf.job + '/approve', { plan_v: conf.plan_v, plan_hash: conf.plan_hash });
  await until(async () => (await api('GET', '/api/ai/jobs/' + done.job)).d.state === 'erledigt' && (await api('GET', '/api/ai/jobs/' + conf.job)).d.state === 'erledigt', 15000);
  const pg = (await api('GET', '/api/notes/' + fest.id)).d;
  await api('PATCH', '/api/notes/' + fest.id, { title: 'Sommerfest', rev: pg.rev, format: 'blocks',
    body: JSON.stringify({ blocks: [{ id: 'h1', type: 'paragraph', data: { text: 'Von Hand umgeschrieben.' } }] }) });

  await openJobs();
  const L = await until(() => p.evaluate(() => {
    const gs = [...document.querySelectorAll('.kj-group')].map((g) => ({ h: g.querySelector('.kj-gh').firstChild.textContent.trim(),
      items: [...g.querySelectorAll('.kj-item')].map((i) => ({ t: i.querySelector('.kj-ititle').textContent, l: i.querySelector('.kj-iline').textContent })) }));
    return gs.length >= 2 && gs;
  }));
  ok('Liste: Gruppen „Wartet auf dich“ und „Fertig“ (leere Gruppen fehlen)', L && L.map((g) => g.h).join('|') === 'Wartet auf dich|Fertig', L);
  const fertig = (L && L.find((g) => g.h === 'Fertig')) || { items: [] };
  ok('Fertig: genaues Wort je Zeile (Erledigt / Abgebrochen)', fertig.items.some((i) => /^Erledigt/.test(i.l)) && fertig.items.some((i) => /^Abgebrochen/.test(i.l)), fertig);
  const wz = (L && L[0].items[0]) || {};
  ok('Wartet auf dich: „Wartet auf dein OK“', /Wartet auf dein OK/.test(wz.l || ''), wz);
  await shot('auftraege-dunkel');
  await p.emulateMedia({ colorScheme: 'light' }); await sleep(300); await shot('auftraege-hell'); await p.emulateMedia({ colorScheme: 'dark' });
  // Tastatur: erster Eintrag per Tab erreichbar, Enter oeffnet das Detail
  await p.focus('.kj-ta');
  let reached = false;
  for (let i = 0; i < 12 && !reached; i++) { await p.keyboard.press('Tab'); reached = await p.evaluate(() => document.activeElement && document.activeElement.classList.contains('kj-item')); }
  ok('Tastatur: Auftrags-Zeile per Tab erreichbar', reached);
  // Detail eines erledigten Auftrags
  await p.evaluate((t) => [...document.querySelectorAll('.kj-item')].find((i) => i.textContent.indexOf(t) >= 0).click(), 'Grillkohle kaufen');
  const D = await until(() => p.evaluate(() => { const d = document.querySelector('.kj-detail:not([hidden])'); return d && d.querySelector('.kj-log li') && {
    title: d.querySelector('.kj-dtitle').textContent, state: d.querySelector('.kj-dstate strong').textContent,
    log: [...d.querySelectorAll('.kj-log li')].map((l) => l.textContent), undo: !!d.querySelector('.kj-undo'),
    secs: [...d.querySelectorAll('.kj-sec h4')].map((h) => h.textContent) }; }));
  ok('Detail: Titel und genaues Zustandswort „Erledigt“', D && D.title === 'Grillkohle kaufen' && D.state === 'Erledigt', D);
  ok('Detail: Plan, Protokoll aus job_event', D && D.secs.includes('Plan') && D.secs.includes('Protokoll') && D.log.some((x) => /Freigegeben/.test(x)) && D.log.some((x) => /Erledigt/.test(x)), D);
  ok('Detail: Rückgängig dauerhaft möglich', D && D.undo);
  await shot('detail-dunkel');
  await p.click('.kj-detail .kj-undo');
  if (await until(() => p.evaluate(() => !!document.querySelector('.hk-ask-box')), 2000)) await p.click('.hk-ask-box .btn-destr');
  ok('Rückgängig im Detail -> „Zurückgenommen“', await until(async () => (await api('GET', '/api/ai/jobs/' + done.job)).d.state === 'zurueckgenommen'));
  ok('Detail zeigt danach „Zurückgenommen“', await until(() => p.evaluate(() => /Zurückgenommen/.test((document.querySelector('.kj-detail .kj-dstate') || {}).textContent || ''))));
  await p.click('.kj-back');
  // Konflikt: Detail des Eintrags in "Sommerfest"
  await until(() => p.evaluate(() => !!document.querySelector('.kj-item')));
  await p.evaluate((id) => window.HKAI && document.querySelectorAll('.kj-item').length && [...document.querySelectorAll('.kj-item')].find((i) => /Ben/.test(i.textContent)).click(), conf.job);
  const K = await until(() => p.evaluate(() => { const d = document.querySelector('.kj-detail:not([hidden]) .kj-changed'); return d && d.textContent; }));
  ok('Konflikt: „Das wurde seitdem geändert.“ statt Rückgängig', K && /Das wurde seitdem geändert\./.test(K), K);
  await p.click('.kj-detail .kj-old');
  const old = await until(() => p.evaluate(() => (document.querySelector('.kj-detail .kj-oldtext') || {}).textContent));
  ok('„Alte Fassung ansehen“ zeigt den Stand vor Marin', old && /Pavillon/.test(old) && !/umgeschrieben/.test(old), old);
  await shot('konflikt-dunkel');
  const pg2 = (await api('GET', '/api/notes/' + fest.id)).d;
  ok('Konflikt: nichts überschrieben', /umgeschrieben/.test(pg2.body || ''));
  await p.click('.kj-back');
  // Zustandswoerter in 5 Sprachen (Systemtext vom Server)
  for (const lg of ['de', 'en', 'es', 'ru', 'th']) {
    await api('PUT', '/api/account/lang', { lang: lg });
    const d = (await api('GET', '/api/ai/jobs')).d;
    ok(`Gruppen in ${lg}: ${WORDS[lg].join(' · ')}`, JSON.stringify(d.groups.map((g) => g.label)) === JSON.stringify(WORDS[lg]), d.groups.map((g) => g.label));
  }
  await api('PUT', '/api/account/lang', { lang: 'de' });
  await api('POST', '/api/ai/jobs/' + wait.job + '/cancel', {});
  ok('keine Skriptfehler', !errs.length, errs);
  await br.close();
  const bad = R.filter((x) => !x[1]).length;
  console.log(`\nKollegin Aufträge (Browser) ${R.length - bad}/${R.length}` + (bad ? ' - FEHLER' : ' - ALLES GRUEN'));
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
