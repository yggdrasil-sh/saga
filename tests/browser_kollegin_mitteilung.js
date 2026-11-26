// Saga 2.6 (Bugs-DB 2270 K-C) - Mitteilungen der Kollegin im Web: Glocke mit Rueckmeldungen zu Auftraegen (job.done /
// job.needs_you: Satz vom Server, Persona-Symbol, „Marin · KI“, nie gebuendelt), Sprung zum Auftrag (/saga?tab=jobs&job=<id>
// von jeder Seite; im Ort „Saga“ ohne Neuladen; Deep-Links saga://job/<id>), Desktop-Huelle mit notify (neutraler Titel,
// Satz nur bei „Inhalt in Mitteilungen: wenn entsperrt“, sonst „In Saga ansehen“; neuer Eintrag ersetzt den alten),
// Einstellungen › Saga › Mitteilungen („Inhalt in Mitteilungen“ wenn entsperrt / nie, „Mitteilungen von Marin“,
// „Ruhe bis 7:00 · Wieder an“), hell/dunkel, schmal (390 px), 200 % Schrift, keine Skriptfehler. Chromium und Firefox (BR=firefox).
// Server ohne Glocken-Eintraege fuer Auftraege (vor kc-srv S4 ai_notify) bzw. ohne notify_quiet_until/cats.jobs: die
// fehlenden Teile werden an die echte Antwort angehaengt (Ausgabe „SIM“) - mit dem Server-Endstand laeuft alles echt.
// Bilder (SHOTS=<ordner>): kc-glocke-dunkel/hell, kc-glocke-schmal-dunkel/hell, kc-glocke-200, kc-einst-dunkel/hell,
// kc-einst-schmal-dunkel/hell, kc-einst-200, kc-detail-dunkel.
// Voraussetzung: tests/kollegin_mitteilung_rig.py setup (Fake-Modell fake-plan, Stufe „Pläne“, Persona Marin, Einwilligung).
//   SAGA_BASE=http://127.0.0.1:8910 SAGA_USER=kcm-web SAGA_PASS=... [SHOTS=/tmp/x] [BR=firefox] node tests/browser_kollegin_mitteilung.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const fs = require('fs'), path = require('path');
const E = process.env, BASE = E.SAGA_BASE || 'http://127.0.0.1:8099', USER = E.SAGA_USER, PASS = E.SAGA_PASS, SHOTS = E.SHOTS || '';
const FF = E.BR === 'firefox', SFX = FF ? '-firefox' : '';
if (!USER || !PASS) { console.error('SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const V = JSON.parse(fs.readFileSync(path.join(__dirname, 'vectors/push_kollegin.json'), 'utf8'));
const R = []; const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x === undefined ? '' : x))); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 8000)) return v; await sleep(150); } }
const UA_MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) SagaApp/3.0 (macOS)';
function macInit() {
  window.__hostMsgs = [];
  window.webkit = { messageHandlers: { hk: { postMessage: (m) => window.__hostMsgs.push(JSON.parse(JSON.stringify(m))) } } };
  window.sagaHostInfo = { kind: 'mac', version: '3.0', caps: ['badge', 'notify'] };
}

(async () => {
  const br = await (FF ? PW.firefox : PW.chromium).launch(!FF && E.PW_CHANNEL ? { channel: E.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1440, height: 900 }, locale: 'de-DE', colorScheme: 'dark' });
  const p = await ctx.newPage(); const errs = [];
  const watch = (pg) => { pg.on('pageerror', (e) => errs.push(e.message)); };
  watch(p);
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  const api = (m, url, b, pg) => (pg || p).evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    const d = await r.json().catch(() => ({})); return { s: r.status, d: d };
  }, [m, url, b]);
  const sse = (b) => p.evaluate(async (b) => {               // POST /api/ai/jobs -> letztes Ereignis {typ, data}
    const r = await fetch('/api/ai/jobs', { method: 'POST', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
    const t = await r.text(); let last = null;
    t.split('\n\n').forEach((ch) => { const ev = /event: (\w+)/.exec(ch), da = /data: (.*)/.exec(ch); if (ev) last = { typ: ev[1], data: da ? JSON.parse(da[1]) : null }; });
    return last;
  }, b);
  async function shot(pg, name) { if (SHOTS) { await sleep(350); await pg.screenshot({ path: SHOTS + '/' + name + SFX + '.png' }); } }
  await api('PUT', '/api/account/lang', { lang: 'de' });
  await api('POST', '/api/first-steps', { answer: 'no' });
  { const st = await api('GET', '/api/ai/status');
    if (st.d && st.d.intro && (!st.d.consent || st.d.consent.state !== 'granted')) await api('POST', '/api/ai/consent', { answer: 'no_ideas', version: st.d.intro.version, host: st.d.intro.host }); }
  const ver = (await api('GET', '/api/version')).d || {};
  const st0 = (await api('GET', '/api/ai/status')).d || {};
  ok('Voraussetzung: KI an, Aufträge an, Persona „Marin“', st0.enabled && st0.jobs && st0.persona && st0.persona.name === 'Marin', st0.persona);

  // --- Testdaten: ein erledigter Auftrag, einer wartet auf das OK ---------------------------------------------------
  const done = (await sse({ recipe: 'aufgabe', fields: { text: 'Grillkohle besorgen' } })).data;
  await api('POST', '/api/ai/jobs/' + done.job + '/approve', { plan_v: done.plan_v, plan_hash: done.plan_hash });
  const wait = (await sse({ recipe: 'aufgabe', fields: { text: 'Pavillon abholen' } })).data;
  ok('Testdaten: Aufträge angelegt', done && done.job && wait && wait.job, { done, wait });
  ok('Auftrag „Grillkohle besorgen“ erledigt', await until(async () => (await api('GET', '/api/ai/jobs/' + done.job)).d.state === 'erledigt', 15000));
  const T1 = V.texts.de.t1, T1done = T1.done, T1needs = T1.needs.replace('Grillkohle besorgen', 'Pavillon abholen');

  // --- Glocke: echte Eintraege (Server-Endstand) oder angehaengt (SIM) ----------------------------------------------
  const real = await until(async () => ((await api('GET', '/api/notifications?limit=30&group=1')).d.items || []).find((i) => i.kind === 'job.done' && +i.job === done.job), 4000);
  const SIM = !real;
  console.log(SIM ? 'SIM  Glocke: Server liefert (noch) keine job.*-Einträge - Einträge werden an /api/notifications angehängt'
    : 'ECHT Glocke: job.done vom Server (' + (ver.features || []).includes('jobpush1') + ' jobpush1)');
  const now = Math.floor(Date.now() / 1000);
  const fakeItems = [
    { id: 990002, kind: 'job.needs_you', created: now - 30, read: false, actor: null, count: 1, access: true, note: null, url: '/saga?tab=jobs&job=' + wait.job,
      job: wait.job, group_key: 'job:' + wait.job, text: T1needs, title: '', ids: [990002], counts: [1] },
    { id: 990001, kind: 'job.done', created: now - 90, read: false, actor: null, count: 1, access: true, note: null, url: '/saga?tab=jobs&job=' + done.job,
      job: done.job, group_key: 'job:' + done.job, text: T1done, title: '', ids: [990001], counts: [1] }];
  async function simBell(c) {
    if (!SIM) return;
    await c.route(/\/api\/notifications\?/, async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const res = await route.fetch(); const d = await res.json();
      const read = new Set((c.__read || [])), count = /limit=0/.test(route.request().url());
      const items = fakeItems.map((x) => Object.assign({}, x, { read: read.has(x.id) })), un = items.filter((x) => !x.read).length;
      if (!count) d.items = items.concat(d.items || []);
      d.unread = (d.unread || 0) + un; d.unread_groups = (d.unread_groups != null ? d.unread_groups : 0) + un;
      return route.fulfill({ response: res, json: d });
    });
    await c.route(/\/api\/notifications\/read$/, async (route) => {
      try { const b = JSON.parse(route.request().postData() || '{}'); c.__read = (c.__read || []).concat(b.ids || []); } catch (e) { /* egal */ }
      return route.continue();
    });
  }
  await simBell(ctx);
  async function openBell(pg) {
    await pg.click('#hk-bell');
    return until(() => pg.evaluate((j) => !!document.querySelector('.lv-item[data-job="' + j + '"]'), done.job), 8000);
  }
  async function bellRow(pg, job) {
    return pg.evaluate((j) => { const b = document.querySelector('.lv-item[data-job="' + j + '"]'); if (!b) return null;
      return { text: b.querySelector('.lv-text').textContent, page: (b.querySelector('.lv-page') || {}).textContent, ai: !!b.querySelector('.lv-av-ai'),
        sym: (b.querySelector('.lv-av-ai') || {}).textContent, initial: !!b.querySelector('.lv-av:not(.lv-av-ai)'), dot: !!b.querySelector('.lv-dot'),
        label: b.getAttribute('aria-label'), rows: document.querySelectorAll('.lv-item[data-job="' + j + '"]').length }; }, job);
  }
  await p.goto(BASE + '/');
  ok('Glocke öffnet mit Auftrags-Einträgen', await openBell(p));
  const rd = await bellRow(p, done.job), rw = await bellRow(p, wait.job);
  ok('Eintrag „erledigt“: Satz vom Server („' + T1done + '“)', rd && rd.text === T1done, rd);
  ok('Eintrag: Persona-Symbol (✦) statt Initialen, Zeile „Marin · KI“', rd && rd.ai && rd.sym === '✦' && !rd.initial && rd.page === 'Marin · KI', rd);
  ok('Eintrag: zugänglicher Name = Satz, Absender, Zeit, Ungelesen', rd && rd.label.indexOf(T1done + ', Marin · KI, ') === 0 && /Ungelesen$/.test(rd.label), rd && rd.label);
  if (SIM) ok('Eintrag „braucht dich“ eigene Zeile (nie gebündelt)', rw && rw.text === T1needs && rw.rows === 1 && rd.rows === 1, rw);
  await shot(p, 'kc-glocke-dunkel');
  await p.emulateMedia({ colorScheme: 'light' }); await shot(p, 'kc-glocke-hell'); await p.emulateMedia({ colorScheme: 'dark' });
  // 200 % Schrift: Liste ohne waagerechten Ueberlauf, Symbol waechst mit
  await p.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  await sleep(300);
  const big = await p.evaluate((j) => { const pn = document.getElementById('lv-panel'), b = document.querySelector('.lv-item[data-job="' + j + '"]');
    const a = b && b.querySelector('.lv-av-ai'), br = b ? b.getBoundingClientRect() : {}, pr = pn.getBoundingClientRect();
    return { over: b ? b.scrollWidth - b.clientWidth : 99, right: br.right - pr.right, head: pn.scrollWidth - pn.clientWidth, av: a ? a.getBoundingClientRect().width : 0, vis: br.height > 0 }; }, done.job);
  ok('200 % Schrift: Auftrags-Zeile ohne waagerechten Überlauf, Symbol wächst mit (' + Math.round(big.av) + ' px)', big.vis && big.over <= 1 && big.right <= 1 && big.av >= 50, big);
  if (big.head > 1) console.log('FUND Glocke bei 200 % Schrift: Kopf der Liste ' + Math.round(big.head) + ' px breiter als die Liste (vorhanden, nicht K-C)');
  await shot(p, 'kc-glocke-200');
  await p.evaluate(() => { document.documentElement.style.fontSize = ''; });
  // Sprung zum Auftrag (von „Heute“)
  await p.click('.lv-item[data-job="' + done.job + '"]');
  const det = await until(() => p.evaluate(() => { const d = document.querySelector('.kj-detail:not([hidden])'); return location.pathname === '/saga' && d && d.querySelector('.kj-dtitle') ? d.querySelector('.kj-dtitle').textContent : null; }), 10000);
  ok('Klick: Ort „Saga“, Reiter „Aufträge“, Detail „Grillkohle besorgen“', det === 'Grillkohle besorgen' && /tab=jobs/.test(await p.evaluate(() => location.search)), det);
  await shot(p, 'kc-detail-dunkel');
  // im Ort „Saga“: ohne Neuladen
  await p.click('.kj-back').catch(() => {});
  await p.evaluate(() => { window.__noReload = 1; });
  await openBell(p);
  if (SIM || await p.evaluate((j) => !!document.querySelector('.lv-item[data-job="' + j + '"]'), wait.job)) {
    await p.click('.lv-item[data-job="' + wait.job + '"]');
    const d2 = await until(() => p.evaluate(() => { const d = document.querySelector('.kj-detail:not([hidden]) .kj-dtitle'); return d ? d.textContent : null; }), 8000);
    ok('im Ort „Saga“: Detail ohne Neuladen („Pavillon abholen“)', d2 === 'Pavillon abholen' && await p.evaluate(() => window.__noReload === 1), d2);
  } else {
    await p.keyboard.press('Escape');
    ok('wartet_ok aus dem Ort „Saga“ -> kein Glocken-Eintrag (Vertrag)', true);
  }
  // direkte Adressen
  await p.goto(BASE + '/saga?tab=jobs&job=' + done.job);
  ok('/saga?tab=jobs&job=<id> öffnet das Detail', await until(() => p.evaluate(() => { const d = document.querySelector('.kj-detail:not([hidden]) .kj-dtitle'); return d && d.textContent === 'Grillkohle besorgen'; })));
  await p.goto(BASE + '/saga?tab=jobs&job=99999999');
  await sleep(1500);
  ok('unbekannter Auftrag: Liste bleibt, kein Fehler', await p.evaluate(() => !document.getElementById('sg-jobs').hidden && !!document.querySelector('#sg-jobs .kj-item') && !document.querySelector('.kj-detail:not([hidden])')));
  // Deep-Links (Vektoren deeplinks + Ziffern-Regel)
  const dl = await p.evaluate((links) => links.map((l) => [l, window.HK && HK.host && HK.host.route ? HK.host.route(l) : 'kein HK.host']), V.deeplinks.map((d) => d.link).concat(['saga://job/abc', 'hlidskjalf://job/7']));
  const wantDl = {}; V.deeplinks.forEach((d) => { wantDl[d.link] = d.route; }); wantDl['saga://job/abc'] = '/saga?tab=jobs'; wantDl['hlidskjalf://job/7'] = '/saga?tab=jobs&job=7';
  ok('Deep-Links saga://saga, saga://jobs, saga://job/<id> (sonst /saga?tab=jobs)', dl.every(([l, r]) => r === wantDl[l]), dl);

  // --- Einstellungen › Saga › Mitteilungen -------------------------------------------------------------------------
  const prefs0 = (await api('GET', '/api/ai/prefs')).d, push0 = (await api('GET', '/api/push')).d;
  const hasQuiet = prefs0 && 'notify_quiet_until' in prefs0, hasCat = (push0.categories || []).includes('jobs');
  console.log((hasQuiet ? 'ECHT' : 'SIM ') + ' notify_quiet_until · ' + (hasCat ? 'ECHT' : 'OFFEN') + ' cats.jobs (Server ' + (hasCat ? 'kennt' : 'kennt noch nicht') + ' den Bereich)');
  const quietAt = now + 3600;
  let quietPut = null;
  async function simQuiet(pg) {
    if (hasQuiet) return api('PUT', '/api/ai/prefs', { notify_quiet_until: quietAt }, pg);
    await pg.route(/\/api\/ai\/prefs$/, async (route) => {
      if (route.request().method() === 'PUT') { const b = JSON.parse(route.request().postData() || '{}'); if ('notify_quiet_until' in b) { quietPut = b; pg.__quietOff = true; } return route.continue(); }
      const res = await route.fetch(); const d = await res.json(); d.notify_quiet_until = pg.__quietOff ? null : quietAt;
      return route.fulfill({ response: res, json: d });
    });
  }
  async function openSet(pg) {
    await pg.goto(BASE + '/');
    await pg.evaluate(() => HKSettings.open('saga'));
    return until(() => pg.evaluate(() => { const b = document.getElementById('hk-saga-notify'); return b && !b.hidden && b.getClientRects().length > 0; }), 8000);
  }
  await simQuiet(p);
  ok('Einstellungen › Saga: Block „Mitteilungen“ sichtbar', await openSet(p));
  const S1 = await p.evaluate(() => { const b = document.getElementById('hk-saga-notify'), sel = document.getElementById('hk-saga-ncontent');
    return { h: b.querySelector('h4').textContent, sec: !!b.closest('.hk-set-saga'), sel: sel && sel.value, opts: sel ? [...sel.options].map((o) => o.textContent) : [],
      lab: sel && sel.closest('label').querySelector('span').textContent, jobs: !!document.getElementById('hk-saga-njobs'),
      jl: document.getElementById('hk-saga-njobs') ? document.getElementById('hk-saga-njobs').closest('label').textContent : '',
      q: (document.getElementById('hk-saga-nquiet') || {}).textContent || '', hint: (b.querySelector('.hk-hint') || {}).textContent }; });
  ok('Block im Abschnitt „Saga“, Überschrift „Mitteilungen“', S1.sec && S1.h === 'Mitteilungen', S1);
  ok('„Inhalt in Mitteilungen“: Wenn entsperrt / Nie, Standard „Wenn entsperrt“', S1.lab === 'Inhalt in Mitteilungen' && S1.opts.join('|') === 'Wenn entsperrt|Nie' && S1.sel === 'unlocked', S1);
  ok('Hinweis nennt „In Saga ansehen“ und Marin', /In Saga ansehen/.test(S1.hint) && /Marin/.test(S1.hint), S1.hint);
  const tm = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' }).format(new Date(quietAt * 1000));
  ok('„Ruhe bis ' + tm + '“ mit „Wieder an“' + (hasQuiet ? '' : ' (SIM)'), S1.q === 'Ruhe bis ' + tm + 'Wieder an', S1.q);
  if (hasCat) ok('„Mitteilungen von Marin“ (Bereich jobs) an', S1.jobs && /Mitteilungen von Marin/.test(S1.jl) && await p.evaluate(() => document.getElementById('hk-saga-njobs').checked), S1);
  else ok('„Mitteilungen von Marin“ erst mit Bereich jobs vom Server (S3 offen) - fehlt still', !S1.jobs, S1);
  await shot(p, 'kc-einst-dunkel');
  await p.emulateMedia({ colorScheme: 'light' }); await shot(p, 'kc-einst-hell'); await p.emulateMedia({ colorScheme: 'dark' });
  // 200 % Schrift: Auswahl passt in die Breite
  await p.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  await sleep(300);
  const bs = await p.evaluate(() => { const s = document.getElementById('hk-saga-ncontent').getBoundingClientRect(), b = document.getElementById('hk-saga-notify').getBoundingClientRect(); return { sr: s.right, br: b.right, w: s.width }; });
  ok('200 % Schrift: Auswahl im Block (nicht abgeschnitten)', bs.w > 0 && bs.sr <= bs.br + 1, bs);
  await p.evaluate(() => { const n = document.getElementById('hk-saga-notify'); n.scrollIntoView({ block: 'center' }); });
  await shot(p, 'kc-einst-200');
  await p.evaluate(() => { document.documentElement.style.fontSize = ''; });
  // „Nie“ speichern -> Server; zurueck
  await p.selectOption('#hk-saga-ncontent', 'never');
  ok('„Nie“ gespeichert (notify_content never)', await until(async () => (await api('GET', '/api/ai/prefs')).d.notify_content === 'never'));
  await openSet(p);
  ok('nach Neuladen steht „Nie“', await p.evaluate(() => document.getElementById('hk-saga-ncontent').value === 'never'));
  await p.selectOption('#hk-saga-ncontent', 'unlocked');
  ok('„Wenn entsperrt“ gespeichert', await until(async () => (await api('GET', '/api/ai/prefs')).d.notify_content === 'unlocked'));
  if (hasCat) {
    await p.click('#hk-saga-njobs');
    ok('„Mitteilungen von Marin“ aus -> cats.jobs false', await until(async () => (await api('GET', '/api/push')).d.settings.cats.jobs === false));
    await p.click('#hk-saga-njobs');
    ok('… und wieder an', await until(async () => (await api('GET', '/api/push')).d.settings.cats.jobs !== false));
  }
  await p.click('#hk-saga-nquiet .btn');
  ok('„Wieder an“: Zeile weg, Server bekommt notify_quiet_until null', await until(async () => !(await p.evaluate(() => !!document.getElementById('hk-saga-nquiet')))
    && (hasQuiet ? (await api('GET', '/api/ai/prefs')).d.notify_quiet_until == null : !!quietPut && quietPut.notify_quiet_until === null)), quietPut);
  ok('Fokus nach „Wieder an“ auf der Auswahl (nicht auf <body>)', await p.evaluate(() => document.activeElement && document.activeElement.id === 'hk-saga-ncontent'));
  // Bereich „Mitteilungen“: Bezeichnung des Bereichs jobs
  if (hasCat) {
    await p.evaluate(() => HKSettings.open('push'));
    const lab = await until(() => p.evaluate(() => { const s = document.getElementById('ps-cat-jobs'); return s && s.closest('label').textContent; }));
    ok('Mitteilungen › Worüber: „Mitteilungen von Marin“', lab === 'Mitteilungen von Marin', lab);
  }

  // --- schmal (390 px) -------------------------------------------------------------------------------------------------
  const ctxM = await br.newContext({ viewport: { width: 390, height: 844 }, locale: 'de-DE', colorScheme: 'dark', storageState: await ctx.storageState() });
  await simBell(ctxM);
  const m = await ctxM.newPage(); watch(m);
  await m.goto(BASE + '/');
  ok('schmal: Glocke mit Auftrags-Eintrag', await openBell(m));
  const ov = await m.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  ok('schmal: kein waagerechter Überlauf', ov <= 1, ov);
  await shot(m, 'kc-glocke-schmal-dunkel');
  await m.emulateMedia({ colorScheme: 'light' }); await shot(m, 'kc-glocke-schmal-hell'); await m.emulateMedia({ colorScheme: 'dark' });
  await m.keyboard.press('Escape');
  await simQuiet(m);
  if (hasQuiet) await api('PUT', '/api/ai/prefs', { notify_quiet_until: quietAt }, m);
  ok('schmal: Einstellungen › Saga › Mitteilungen', await openSet(m));
  await m.evaluate(() => document.getElementById('hk-saga-notify').scrollIntoView({ block: 'center' }));
  const ovS = await m.evaluate(() => { const b = document.getElementById('hk-saga-notify').getBoundingClientRect(); return b.right - window.innerWidth; });
  ok('schmal: Block passt in die Breite', ovS <= 1, ovS);
  await shot(m, 'kc-einst-schmal-dunkel');
  await m.emulateMedia({ colorScheme: 'light' }); await shot(m, 'kc-einst-schmal-hell');
  if (hasQuiet) await api('PUT', '/api/ai/prefs', { notify_quiet_until: null }, m);
  await ctxM.unrouteAll({ behavior: 'ignoreErrors' }); await ctxM.close();

  // --- Desktop-Huelle mit notify: live eintreffender Eintrag (Ereignis-Strom nachgebildet) -------------------------------
  const ctxH = await br.newContext({ viewport: { width: 1300, height: 860 }, locale: 'de-DE', userAgent: UA_MAC, storageState: await ctx.storageState() });
  await ctxH.addInitScript(macInit);
  const liveItem = (id, kind, job, text) => ({ id, kind, created: Math.floor(Date.now() / 1000), read: false, actor: null, count: 1, access: true, note: null,
    url: '/saga?tab=jobs&job=' + job, job, group_key: 'job:' + job, text, title: '' });
  let stream = [], served = [];                  // served: was der Server fuer /api/notifications haelt (je Auftrag der neueste)
  await ctxH.route(/\/api\/events/, (route) => {
    const evs = stream; stream = [];
    const body = 'retry: 1200\nevent: hello\ndata: {}\n\n' + evs.map((d) => 'event: notification\ndata: ' + JSON.stringify(d) + '\n\n').join('');
    return route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' }, body });
  });
  await ctxH.route(/\/api\/notifications\?/, async (route) => {
    const res = await route.fetch(); const d = await res.json();
    if (!/limit=0/.test(route.request().url())) d.items = served.concat((d.items || []).filter((x) => !/^job\./.test(x.kind)));
    return route.fulfill({ response: res, json: d });
  });
  const h = await ctxH.newPage(); watch(h);
  await h.goto(BASE + '/notes');
  await until(() => h.evaluate(() => !!document.getElementById('hk-bell')));
  await h.click('#hk-bell'); await sleep(800);                 // Liste geladen - live Eintreffendes kommt hinzu
  const doneW = liveItem(990102, 'job.done', wait.job, T1done.replace('Grillkohle besorgen', 'Pavillon abholen'));
  served = [doneW];
  stream = [{ item: liveItem(990101, 'job.needs_you', wait.job, T1needs), unread: 1 }, { item: doneW, unread: 1 }];
  const nt = await until(async () => { const ms = await h.evaluate(() => (window.__hostMsgs || []).filter((x) => x.action === 'notify')); return ms.length >= 2 && ms; }, 8000);
  ok('Hülle: Live-Eintrag -> notify mit neutralem Titel „Marin · KI – braucht dich“, Satz (Inhalt: wenn entsperrt)', nt && nt[0].title === V.texts.de.title.needs && nt[0].body === T1needs
    && nt[0].url === '/saga?tab=jobs&job=' + wait.job && nt[0].tag === 'job-' + wait.job && nt[0].kind === 'job.needs_you', nt);
  ok('Hülle: „erledigt“ -> „Marin · KI – Rückmeldung“, gleicher tag', nt && nt[1].title === V.texts.de.title.feedback && nt[1].tag === 'job-' + wait.job, nt && nt[1]);
  const rows = await until(() => h.evaluate((j) => { const r = document.querySelectorAll('.lv-item[data-job="' + j + '"]'); return r.length ? [r.length, r[0].querySelector('.lv-text').textContent] : null; }, wait.job));
  ok('Glocke: neuer Eintrag desselben Auftrags ersetzt den alten (eine Zeile)', rows && rows[0] === 1 && /^Erledigt/.test(rows[1]), rows);
  await api('PUT', '/api/ai/prefs', { notify_content: 'never' }, h);
  await h.close();                                             // sonst holt deren Wiederverbindung die naechsten Ereignisse ab
  stream = [{ item: liveItem(990103, 'job.done', done.job, T1done), unread: 2 }];
  const h2 = await ctxH.newPage(); watch(h2);
  await h2.goto(BASE + '/notes');
  const nt2 = await until(async () => { const ms = await h2.evaluate(() => (window.__hostMsgs || []).filter((x) => x.action === 'notify')); return ms.length && ms; }, 8000);
  ok('Hülle bei „Inhalt: nie“: nur „In Saga ansehen“ (kein Titel des Auftrags)', nt2 && nt2[0].title === V.texts.de.title.feedback && nt2[0].body === V.texts.de.body
    && JSON.stringify(nt2).indexOf('Grillkohle') < 0, nt2);
  await api('PUT', '/api/ai/prefs', { notify_content: 'unlocked' }, h2);
  await ctxH.unrouteAll({ behavior: 'ignoreErrors' }); await ctx.unrouteAll({ behavior: 'ignoreErrors' });
  await ctxH.close();

  // --- echter Service Worker (nur Chromium, Server mit jobpush1): Nutzlast mit echten Token per CDP zustellen ----------
  if (!FF && (ver.features || []).includes('jobpush1')) {
    const { execFileSync } = require('child_process');
    const pyMsg = (job, kind) => JSON.parse(execFileSync('python', ['tests/kollegin_mitteilung_rig.py', 'msg', String(job), kind],
      { env: Object.assign({}, process.env, { PYTHONPATH: 'tests/pgshim:.' }), encoding: 'utf8' }).trim().split('\n').pop());
    await ctx.grantPermissions(['notifications'], { origin: BASE });
    const s = await ctx.newPage(); watch(s);
    await s.goto(BASE + '/');
    await s.evaluate(() => navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(() => navigator.serviceWorker.ready));
    const swk = ctx.serviceWorkers()[0] || await ctx.waitForEvent('serviceworker');
    const cdp = await ctx.newCDPSession(s);
    let regId = null;
    cdp.on('ServiceWorker.workerRegistrationUpdated', (e) => { (e.registrations || []).forEach((r) => { if (!r.isDeleted && r.scopeURL === BASE + '/') regId = r.registrationId; }); });
    await cdp.send('ServiceWorker.enable');
    await until(() => regId, 5000);
    const mDone = pyMsg(done.job, 'job.done');
    ok('echte Nutzlast: job.done mit detail und Token quiet', mDone.v === 3 && mDone.detail && mDone.detail.t1 === T1done
      && (mDone.actions || []).some((a) => a.a === 'quiet'), { v: mDone.v, a: (mDone.actions || []).map((a) => a.a), t1: mDone.detail && mDone.detail.t1 });
    await cdp.send('ServiceWorker.deliverPushMessage', { origin: BASE, registrationId: String(regId), data: JSON.stringify(mDone) });
    const shown = await until(() => s.evaluate((tag) => navigator.serviceWorker.ready.then((r) => r.getNotifications({ tag })).then((ns) => ns.length ? ns.map((n) => ({
      title: n.title, body: n.body, actions: (n.actions || []).map((a) => a.action + ':' + a.title), url: n.data.url, tok: Object.keys(n.data.tok || {}) })) : null), 'job-' + done.job), 8000);
    const sn = shown && shown[0];
    ok('echter SW: Titel t1, Text t2, Knöpfe „Gerade nicht“ + „Ansehen“, kein Rückgängig', sn && sn.title === T1done && sn.body === mDone.detail.t2
      && sn.actions.join('|') === 'quiet:Gerade nicht|open:Ansehen' && sn.tok.join() === 'quiet' && sn.url === BASE + '/saga?tab=jobs&job=' + done.job, shown);
    // „Gerade nicht“ im SW (dieselbe Funktion wie der Knopf): act mit Sitzung + X-Saga -> 200, Ruhe bis 07:00
    const r1 = await swk.evaluate((tag) => self.registration.getNotifications({ tag }).then((ns) => jobAct(ns[0].data, 'quiet')).then(() => 'ok', (e) => String(e)), 'job-' + done.job);
    const qu = (await api('GET', '/api/ai/prefs')).d.notify_quiet_until;
    const q7 = qu ? new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: E.TZ || undefined }).format(new Date(qu * 1000)) : '';
    ok('echter SW: „Gerade nicht“ -> act 200, notify_quiet_until gesetzt (' + q7 + ')', r1 === 'ok' && qu > now, { r1, qu });
    // derselbe Token noch einmal -> 410 used -> Fenster; ohne Klick verweigert der Browser es -> Hinweis „Bitte in Saga ansehen“
    const r2 = await swk.evaluate((d) => jobAct(d, 'quiet').then(() => self.registration.getNotifications({ tag: 'saga-job-hint' })).then((ns) => ns.map((n) => [n.title, n.data.url])),
      { url: BASE + '/saga?tab=jobs&job=' + done.job, kind: 'job.done', job: done.job, pv: mDone.pv, tok: { quiet: mDone.actions.find((a) => a.a === 'quiet').t }, lang: 'de' });
    const used = await api('POST', '/api/ai/jobs/' + done.job + '/act', { action: 'quiet', atok: mDone.actions.find((a) => a.a === 'quiet').t, pv: mDone.pv });
    ok('echter SW: zweiter Druck -> 410 used -> Hinweis „Bitte in Saga ansehen“ mit dem Auftrag', used.s === 410 && used.d.code === 'used'
      && r2.some((x) => x[0] === V.texts.de.hint && x[1] === BASE + '/saga?tab=jobs&job=' + done.job), { used, r2 });
    await api('PUT', '/api/ai/prefs', { notify_quiet_until: null });
    // „Später“ an einem Auftrag, der auf das OK wartet
    // „Später“ an einem Auftrag von ausserhalb des Orts „Saga“ (wartet_ok aus Saga meldet sich nicht - Vertrag)
    const ext = +execFileSync('python', ['tests/kollegin_mitteilung_rig.py', 'needs', 'Pavillon abholen'],
      { env: Object.assign({}, process.env, { PYTHONPATH: 'tests/pgshim:.' }), encoding: 'utf8' }).trim().split('\n').pop();
    const mWait = pyMsg(ext, 'job.needs_you') || {};
    ok('echte Nutzlast: job.needs_you (von außen) mit Token later' + ((mWait.actions || []).some((a) => a.a === 'approve') ? ' und approve' : ''),
      mWait.v === 3 && mWait.detail && mWait.detail.t1 === T1needs, { a: (mWait.actions || []).map((a) => a.a), t1: mWait.detail && mWait.detail.t1 });
    const lt = (mWait.actions || []).find((a) => a.a === 'later');
    if (lt) {
      await cdp.send('ServiceWorker.deliverPushMessage', { origin: BASE, registrationId: String(regId), data: JSON.stringify(mWait) });
      const nw = await until(() => s.evaluate((tag) => navigator.serviceWorker.ready.then((r) => r.getNotifications({ tag })).then((ns) => ns.length ? (ns[0].actions || []).map((a) => a.action) : null), 'job-' + ext), 8000);
      ok('echter SW: „braucht dich“ mit „Ansehen“ + „Später“ (nie „Passt, los“)', nw && nw.join() === 'open,later', nw);
      const r3 = await swk.evaluate((tag) => self.registration.getNotifications({ tag }).then((ns) => jobAct(ns[0].data, 'later')).then(() => 'ok', (e) => String(e)), 'job-' + ext);
      const reuse = await api('POST', '/api/ai/jobs/' + ext + '/act', { action: 'later', atok: lt.t, pv: mWait.pv });
      ok('echter SW: „Später“ -> act 200 (Token danach verbraucht)', r3 === 'ok' && reuse.s === 410, { r3, reuse });
    } else ok('echte Nutzlast „braucht dich“ hat einen later-Token', false, mWait.actions);
    await api('POST', '/api/ai/jobs/' + ext + '/cancel', {});
    await s.evaluate(() => navigator.serviceWorker.ready.then((r) => r.getNotifications()).then((ns) => ns.forEach((n) => n.close())));
    await s.close();
  } else console.log('OFFEN echter Service Worker: nur Chromium mit Server-Feature jobpush1');

  await api('POST', '/api/ai/jobs/' + wait.job + '/cancel', {});   // hoechstens 5 offene Auftraege je Konto (naechster Lauf)
  ok('keine Skriptfehler', !errs.length, errs);
  await br.close();
  const bad = R.filter((r) => !r[1]);
  console.log(`\n${R.length - bad.length}/${R.length} OK${SIM ? ' (Glocke SIM)' : ''}${hasQuiet ? '' : ' (Ruhe SIM)'}${hasCat ? '' : ' (cats.jobs offen)'}`);
  process.exit(bad.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
