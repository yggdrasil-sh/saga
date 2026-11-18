// Vorgemerkte Eingabe nach 423 (Seite anderswo gesperrt, Ereignis verpasst) - Pruefer-Reihe zu browser_sperre_live.js.
// Laeuft im Editor des Servers (Tiptap ohne live bzw. Editor.js, je nach Admin-Einstellung editor.engine).
// 1) Inhalt getippt -> 423 -> Sperre sichtbar, Inhalt bleibt vorgemerkt -> Schloss -> Inhalt gespeichert.
// 2) Titel vorgemerkt, danach meldet ein Baustein eine Aenderung (HKPages.k.dirty(): Datenbank-Block, pm-live-Rueckfall,
//    Erwaehnungen ...) -> die Vormerkung darf dabei nicht still verfallen -> Schloss -> Titel gespeichert.
// 3) Vormerkung + Neu laden: der Browser fragt vor dem Verlassen (beforeunload), statt den Text still zu verwerfen.
// 4) Vormerkung + anderes Geraet entsperrt UND setzt einen anderen Titel: kein stilles Ueberschreiben ohne Spur
//    (Server fuehrt zusammen bzw. Konflikt) und keine 423-Schleife.
// 5) Peek: Schliessen meldet die Anwesenheit ab (leave), verdeckt kein Nachmelden; Ereignisse kommen genau einmal an.
// Lokal: HK_TEST_BASE=... CK=<Cookie> DATABASE_URL=... node tests/browser_sperre_vormerk.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const { execFileSync } = require('child_process');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, DBURL = process.env.DATABASE_URL;
if (!BASE || !CK || !DBURL) { console.error('HK_TEST_BASE, CK, DATABASE_URL setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);
const lockDb = (id, on) => execFileSync('psql', [DBURL, '-qc', `UPDATE note SET locked=${on ? 1 : 0} WHERE id=${id}`]);

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function session() {
    const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = []; p.c423 = 0; p.reqs = [];
    p.on('pageerror', (e) => p.errs.push(e.message));
    p.on('response', (r) => { if (r.request().method() === 'PATCH' && r.status() === 423) p.c423++; });
    p.on('request', (r) => p.reqs.push({ t: Date.now(), m: r.method(), u: r.url(), f: r.frame() && r.frame().url(), b: r.postData() }));
    await p.addInitScript(() => { document.addEventListener('hk-live', (e) => { if (e.detail && e.detail.type === 'live.hello') window.__hello = true; }); });
    await p.goto(BASE + '/notes'); await sleep(400);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return { ctx, p };
  }
  const { p } = await session(), api = p.api;
  const other = (await session()).p;
  const eng = (await api('GET', '/api/account/editor')).engine;
  console.log('     Editor: ' + eng);
  const pg = (await api('POST', '/api/notes', { title: 'Vormerk ' + RUN })).id;
  await api('PATCH', '/api/notes/' + pg, { format: 'blocks', body: JSON.stringify({ blocks: [{ id: 'a1', type: 'paragraph', data: { text: 'Start' } }] }) });

  const st = () => p.evaluate(() => {
    const c = HKPages.k.cur(), b = document.getElementById('pg-lock'), t = document.getElementById('pg-title'), s = document.getElementById('pg-state');
    return { locked: !!(c && c.locked), icon: !!b && !b.hidden, ro: !!t && t.readOnly, title: t ? t.value : null, state: s ? s.textContent : '',
             body: (document.getElementById('pg-editor') || {}).textContent || '' };
  });
  async function until(fn, ms = 6000) { const t0 = Date.now(); let v; while (Date.now() - t0 < ms) { v = await fn(); if (v) return v; await sleep(200); } return v; }
  async function open(id) {
    lockDb(id, false);                                  // jeder Fall beginnt entsperrt
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
    for (let i = 0; i < 60; i++) { if (await p.evaluate((id) => !!(HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector('#pg-editor [contenteditable="true"]')), id)) break; await sleep(100); }
    for (let i = 0; i < 40; i++) { if (await p.evaluate(() => !!window.__hello)) break; await sleep(250); }
    await sleep(600);
  }
  async function titleTo423(txt) {
    lockDb(pg, true); p.c423 = 0;
    await p.click('#pg-title'); await p.keyboard.press('End'); await p.keyboard.type(txt, { delay: 30 });
    return await until(async () => { const s = await st(); return s.locked && s.icon ? s : null; }, 8000) || await st();
  }
  const unlockClick = async () => { if ((await st()).icon) await p.click('#pg-lock', { timeout: 3000 }); };

  // --- 1. Inhalt nach 423 (nicht live: mit notes.live geht der Inhalt ueber Yjs, pm-live verwirft ihn mit Hinweis) --
  await open(pg);
  const live = await p.evaluate(() => !!(HKPages.k.liveBound && HKPages.k.liveBound()));
  console.log('     live gebunden: ' + live);
  if (!live) {
  lockDb(pg, true); p.c423 = 0;
  await p.click('#pg-editor [contenteditable="true"] p, #pg-editor .ce-paragraph');
  await p.keyboard.press('End'); await p.keyboard.type(' Inhalt01', { delay: 30 });
  const b1 = await until(async () => { const s = await st(); return s.locked && s.icon ? s : null; }, 8000) || await st();
  ok('Inhalt: 423 zeigt die Sperre', b1.locked && b1.icon, b1);
  ok('Inhalt: Eingabe bleibt sichtbar', /Inhalt01/.test(b1.body), b1.body);
  await sleep(2500);
  ok('Inhalt: keine 423-Schleife', p.c423 >= 1 && p.c423 <= 2, p.c423);
  await unlockClick();
  const n1 = await until(async () => { const n = await api('GET', '/api/notes/' + pg); return /Inhalt01/.test(n.body || '') && !n.locked ? n : null; }, 6000);
  ok('Inhalt: nach dem Entsperren gespeichert', !!n1, (await api('GET', '/api/notes/' + pg)).body);
  }

  // --- 2. Vormerkung + Baustein meldet Aenderung ----------------------------------------------------------------
  await open(pg);
  const s2 = await titleTo423('X2');
  ok('Titel: 423 zeigt die Sperre', s2.locked && s2.icon && s2.ro, s2);
  await p.evaluate(() => HKPages.k.dirty());
  await sleep(1500);
  await unlockClick();
  const n2 = await until(async () => { const n = await api('GET', '/api/notes/' + pg); return /X2$/.test(n.title) && !n.locked ? n : null; }, 6000);
  ok('Titel: Vormerkung ueberlebt eine Baustein-Aenderung (k.dirty) und wird gespeichert', !!n2, (await api('GET', '/api/notes/' + pg)).title);

  // --- 3. Vormerkung + Neu laden -> nachfragen ------------------------------------------------------------------
  await open(pg);
  await titleTo423('X3');
  const asks = await p.evaluate(() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; });
  ok('Neu laden mit Vormerkung: Browser fragt nach (beforeunload)', asks, asks);
  const plain = await p.evaluate(() => { const e = new Event('beforeunload', { cancelable: true }); return e; }).catch(() => null);
  void plain;
  await unlockClick();
  await until(async () => { const n = await api('GET', '/api/notes/' + pg); return /X3$/.test(n.title) ? n : null; }, 6000);
  const asks2 = await p.evaluate(() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; });
  ok('Ohne Vormerkung: keine Nachfrage', !asks2, asks2);

  // --- 4. Vormerkung + fremdes Entsperren mit neuem Titel ----------------------------------------------------------
  await open(pg);
  const before = (await api('GET', '/api/notes/' + pg)).title;
  await titleTo423('Mein4');
  const c423 = p.c423;
  const cur = await other.api('GET', '/api/notes/' + pg);
  await other.api('PATCH', '/api/notes/' + pg, { locked: false, title: 'Fremd4', rev: cur.rev });
  await sleep(2500);
  await unlockClick(); await sleep(2500);
  const n4 = await api('GET', '/api/notes/' + pg), s4 = await st();
  const vers = await api('GET', '/api/notes/' + pg + '/versions').catch(() => null);
  console.log('     Fall 4: Server-Titel ' + JSON.stringify(n4.title) + ', Feld ' + JSON.stringify(s4.title) + ', Status ' + JSON.stringify(s4.state) +
              ', Versionen ' + (vers && (vers.versions || vers).length));
  const conflictShown = await p.evaluate(() => { const b = document.querySelector('.pg-conflict, #pg-conflict'); return !!(b && !b.hidden); });
  ok('Fremder Titel: nicht still ueberschrieben (Fremd4 steht noch, Konflikt sichtbar oder Zusammenfuehrung meldet es)',
     n4.title === 'Fremd4' || conflictShown || /überschrieben|Konflikt|geändert/i.test(s4.state), { n: n4.title, s: s4.state, conflictShown, before });
  ok('Fremder Titel: keine 423-Schleife', p.c423 - c423 <= 1, p.c423 - c423);

  // --- 4b. Vormerkung + Wechsel auf eine andere Seite: die Vormerkung wandert nicht in die neue Seite (kein PATCH dort)
  const pgB = (await api('POST', '/api/notes', { title: 'Andere ' + RUN })).id;
  await api('PATCH', '/api/notes/' + pgB, { format: 'blocks', body: JSON.stringify({ blocks: [{ id: 'b1', type: 'paragraph', data: { text: 'Bleibt' } }] }) });
  await api('PATCH', '/api/notes/' + pg, { locked: false });
  await open(pg);
  await titleTo423('Weg5');
  const revB = (await api('GET', '/api/notes/' + pgB)).rev, tB = Date.now();
  await p.evaluate((id) => { location.hash = '#' + id; }, pgB);
  await until(async () => p.evaluate((id) => !!(HKPages.k.cur() && HKPages.k.cur().id === id), pgB), 6000);
  await sleep(1200);
  await p.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); });
  await sleep(1500);
  await p.evaluate(() => { delete document.hidden; });
  const patB = p.reqs.filter((r) => r.t >= tB && r.m === 'PATCH' && r.u.endsWith('/api/notes/' + pgB));
  const nB = await api('GET', '/api/notes/' + pgB);
  ok('Seitenwechsel: Vormerkung der gesperrten Seite schreibt nicht in die neue Seite', patB.length === 0 && nB.rev === revB,
     { patches: patB.map((r) => r.b && r.b.slice(0, 80)), rev: [revB, nB.rev] });

  // --- 5. Peek: Anwesenheit an/ab, Ereignisse einmal ------------------------------------------------------------
  const db = (await api('POST', '/api/notes', { title: 'Vormerk DB ' + RUN, kind: 'database' })).id;
  const row = (await api('POST', '/api/notes', { title: 'Zeile', parent_id: db })).id;
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + db);
  await p.waitForSelector('.db-row .db-open', { timeout: 15000 }).catch(() => null);
  for (let i = 0; i < 40; i++) { if (await p.evaluate(() => !!window.__hello)) break; await sleep(250); }
  await p.evaluate(() => { const b = document.querySelector('.db-row .db-open'); if (b) b.click(); });
  let f = null;
  for (let i = 0; i < 60; i++) {
    f = p.frames().find((x) => /embed=1/.test(x.url()));
    if (f && await f.evaluate((id) => !!(window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id), row).catch(() => false)) break;
    await sleep(250);
  }
  await sleep(1500);
  const pres = () => p.reqs.filter((r) => /\/api\/presence$/.test(r.u) && /embed=1/.test(r.f || ''));
  const pingRow = pres().filter((r) => r.b && JSON.parse(r.b).note_id === row && !JSON.parse(r.b).leave);
  ok('Peek meldet die Zeile als offen (nur die Zeile)', pingRow.length >= 1 && pres().every((r) => JSON.parse(r.b).note_id === row), pres().map((r) => r.b));
  // Ereignisse: Rahmen zaehlt peek-remote
  await f.evaluate(() => { window.__pr = 0; window.addEventListener('message', (e) => { if (e.data && e.data.hk === 'peek-remote') window.__pr++; }); });
  await other.api('PATCH', '/api/notes/' + row, { icon: '📌' });
  await sleep(2500);
  const pr = await f.evaluate(() => window.__pr);
  ok('Peek: ein Ereignis je fremder Aenderung (nicht doppelt)', pr === 1, pr);
  const t0 = Date.now();
  await p.keyboard.press('Escape'); await sleep(800);
  const peekClosed = await p.evaluate(() => document.getElementById('pg-peek').hidden);
  if (!peekClosed) { await p.evaluate(() => { const b = document.getElementById('peek-close'); if (b) b.click(); }); await sleep(800); }
  const lv = p.reqs.filter((r) => r.t >= t0 && /\/api\/presence$/.test(r.u) && r.b && JSON.parse(r.b).leave && JSON.parse(r.b).note_id === row);
  ok('Peek zu: Anwesenheit abgemeldet (leave)', lv.length >= 1, lv.length);
  await sleep(22000);
  const late = p.reqs.filter((r) => r.t >= t0 + 1500 && /\/api\/presence$/.test(r.u) && /embed=1/.test(r.f || ''));
  ok('Peek zu: kein Nachmelden aus dem verdeckten Rahmen (22 s)', late.length === 0, late.map((r) => r.b));

  ok('Keine Seitenfehler', !p.errs.length && !other.errs.length, p.errs.concat(other.errs));
  for (const id of [row, db, pg, pgB]) { lockDb(id, false); await api('DELETE', '/api/notes/' + id).catch(() => {}); }
  await br.close();
  const bad = res.filter((x) => !x).length;
  console.log(bad ? `${bad} FEHLER` : 'ALLES GRUEN');
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
