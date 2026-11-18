// Seiten-Sperre waehrend die Seite im Browser offen ist (2.5, Feldfehler: App sperrt die Seite, der Browser zeigt kein
// Schloss, laesst den Titel tippen und meldet nur "Nicht gespeichert: Seite ist gesperrt"; der Titel geht beim Verlassen
// verloren). Seite als Zeile einer Datenbank (wie im Feld), Tiptap, mit notes.live an live gebunden.
// 1) Ein anderes Geraet desselben Kontos sperrt die offene Seite (PATCH locked) -> der Browser zieht die Sperre still nach:
//    Schloss sichtbar, Titel schreibgeschuetzt, S.cur.locked. Entsperren dort -> Browser wieder frei.
// 2) Sperre ohne Ereignis (direkt in der Datenbank, z. B. verpasstes Ereignis): Titel tippen -> 423 -> Browser zeigt die
//    Sperre (Schloss, Hinweis), der getippte Titel bleibt im Feld und wird nach dem Entsperren (Klick aufs Schloss)
//    gespeichert - nicht still verloren, keine Endlosschleife von 423-Versuchen.
// Lokal: HK_TEST_BASE=... CK=<Cookie hk-test-a> DATABASE_URL=... node tests/browser_sperre_live.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const { execFileSync } = require('child_process');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, DBURL = process.env.DATABASE_URL;
if (!BASE || !CK || !DBURL) { console.error('HK_TEST_BASE, CK, DATABASE_URL setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function session() {
    const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = []; p.c423 = 0;
    p.on('pageerror', (e) => p.errs.push(e.message));
    p.on('response', (r) => { if (r.request().method() === 'PATCH' && r.status() === 423) p.c423++; });
    await p.addInitScript(() => { document.addEventListener('hk-live', (e) => { if (e.detail && e.detail.type === 'live.hello') window.__hello = true; }); });
    await p.goto(BASE + '/notes'); await sleep(400);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return { ctx, p };
  }
  const { p } = await session(), api = p.api;
  const other = (await session()).p;                    // "anderes Geraet" (eigener Tab, eigene Kennung)
  const ed = await api('GET', '/api/account/editor');
  if (ed.choice) { await api('PUT', '/api/account/editor', { tiptap: true }); await p.reload(); await sleep(400); }
  const db = (await api('POST', '/api/notes', { title: 'Sperre DB ' + RUN, kind: 'database' })).id;
  const row = (await api('POST', '/api/notes', { title: '', parent_id: db })).id;
  await api('PATCH', '/api/notes/' + row, { format: 'blocks', body: JSON.stringify({ blocks: [{ id: 'a1', type: 'paragraph', data: { text: 'Inhalt' } }] }) });
  await api('PATCH', '/api/notes/' + row, { tags: ['sperrtag'], icon: '📄' });

  const st = () => p.evaluate(() => {
    const c = HKPages.k.cur(), b = document.getElementById('pg-lock'), t = document.getElementById('pg-title'), s = document.getElementById('pg-state');
    return { locked: !!(c && c.locked), icon: !!b && !b.hidden, ro: !!t && t.readOnly, title: t ? t.value : null,
             state: s ? s.textContent : '', live: !!(HKPages.k.liveBound && HKPages.k.liveBound()) };
  });
  async function openRow() {
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + row);
    for (let i = 0; i < 60; i++) { if (await p.evaluate((id) => !!(HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector('#pg-editor .pm-doc')), row)) break; await sleep(100); }
    for (let i = 0; i < 20; i++) { if ((await st()).live) break; await sleep(250); }
    for (let i = 0; i < 40; i++) { if (await p.evaluate(() => !!window.__hello)) break; await sleep(250); }   // Ereigniskanal steht
    await sleep(500);
  }
  async function until(fn, ms = 6000) { const t0 = Date.now(); let v; while (Date.now() - t0 < ms) { v = await fn(); if (v) return v; await sleep(200); } return v; }

  // --- 1. Sperre von einem anderen Geraet, waehrend die Seite offen ist -------------------------------------------
  await openRow();
  const s0 = await st();
  console.log('     live gebunden: ' + s0.live);
  ok('Ausgang: Seite offen, nicht gesperrt, Titel bearbeitbar', !s0.locked && !s0.icon && !s0.ro, s0);
  await other.api('PATCH', '/api/notes/' + row, { locked: true });
  const s1 = await until(async () => { const s = await st(); return s.locked && s.icon && s.ro ? s : null; }) || await st();
  ok('Sperre vom anderen Geraet kommt an: Schloss sichtbar, Titel schreibgeschuetzt', s1.locked && s1.icon && s1.ro, s1);
  // Bug 2124: Server lehnt Titelbild/Symbol/Tags auf gesperrten Seiten ab (423) - das Web bietet sie gar nicht an und sendet nichts
  const meta = [];
  const onReq = (r) => { if (r.method() === 'PATCH' && /"(icon|cover|cover_pos|tags)"/.test(r.postData() || '')) meta.push(r.postData()); };
  p.on('request', onReq);
  const vis = await p.evaluate(() => { const v = (q) => { const e = document.querySelector(q); return !!e && e.offsetParent !== null; };
    return { tagX: v('#pg-tags .tag button'), tagAdd: v('#pg-tags .tag-add'), addCover: v('#pg-addcover'), coverBtns: v('.cover-btns') }; });
  ok('Gesperrt: Tag entfernen/hinzufuegen und Titelbild-Knoepfe ausgeblendet', !vis.tagX && !vis.tagAdd && !vis.addCover && !vis.coverBtns, vis);
  await p.evaluate(() => { const b = document.querySelector('#pg-tags .tag button'); if (b) b.click(); const i = document.getElementById('pg-icon'); if (i) i.click(); });
  await sleep(800); await p.keyboard.press('Escape').catch(() => {});
  p.off('request', onReq);
  ok('Gesperrt: Klick auf Tag-x/Symbol schickt kein PATCH mit Symbol/Titelbild/Tags', meta.length === 0, meta);
  await other.api('PATCH', '/api/notes/' + row, { locked: false });
  const s2 = await until(async () => { const s = await st(); return !s.locked && !s.icon && !s.ro ? s : null; }) || await st();
  ok('Entsperren vom anderen Geraet kommt an: Schloss weg, Titel bearbeitbar', !s2.locked && !s2.icon && !s2.ro, s2);

  // --- 2. Sperre ohne Ereignis -> 423 beim Speichern des Titels ----------------------------------------------------
  await openRow();
  execFileSync('psql', [DBURL, '-qc', `UPDATE note SET locked=1 WHERE id=${row}`]);
  p.c423 = 0;
  await p.click('#pg-title'); await p.keyboard.type('Test01', { delay: 30 });
  const s3 = await until(async () => { const s = await st(); return s.locked && s.icon && s.ro ? s : null; }, 8000) || await st();
  ok('423 beim Titel: Browser zeigt die Sperre (Schloss, Titel schreibgeschuetzt)', s3.locked && s3.icon && s3.ro, s3);
  ok('423: Hinweis nennt die Sperre', /gesperrt/i.test(s3.state), s3.state);
  ok('423: getippter Titel bleibt im Feld', s3.title === 'Test01', s3.title);
  await sleep(4000);
  ok('423: keine Wiederholungsschleife (hoechstens 2 Versuche)', p.c423 >= 1 && p.c423 <= 2, p.c423);
  // Eigentuemer klickt aufs Schloss -> entsperren, ausstehender Titel wird gespeichert
  if ((await st()).icon) await p.click('#pg-lock', { timeout: 3000 }); else ok('Schloss zum Entsperren da', false, await st());
  const saved = await until(async () => { const n = await api('GET', '/api/notes/' + row); return n.title === 'Test01' && !n.locked ? n : null; }, 6000);
  ok('Nach dem Entsperren ist der Titel gespeichert (nicht still verloren)', !!saved, await api('GET', '/api/notes/' + row));
  const s4 = await st();
  ok('Nach dem Entsperren: Schloss weg, Titel bearbeitbar', !s4.locked && !s4.icon && !s4.ro, s4);

  // --- 3. Peek (Zeile aus der Datenbank-Ansicht im Rahmen /notes?embed=1, wie im Feld): Titelbild und Sperre eines
  //        anderen Geraets kommen im Rahmen an (vorher: kein Ereigniskanal im Rahmen, keine Anwesenheit fuer die Zeile)
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + db);
  await p.waitForSelector('.db-row .db-open', { timeout: 15000 }).catch(() => null);
  for (let i = 0; i < 40; i++) { if (await p.evaluate(() => !!window.__hello)) break; await sleep(250); }
  await p.evaluate(() => { const b = document.querySelector('.db-row .db-open'); if (b) b.click(); });
  const fr = async () => p.frames().find((x) => /embed=1/.test(x.url()));
  let f = null;
  for (let i = 0; i < 60; i++) {
    f = await fr();
    if (f && await f.evaluate((id) => !!(window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id), row).catch(() => false)) break;
    await sleep(250);
  }
  ok('Peek offen mit der Zeile', !!f && await f.evaluate((id) => HKPages.k.cur().id === id, row).catch(() => false));
  await sleep(1500);                                   // Rahmen meldet sich als anwesend
  await other.api('PATCH', '/api/notes/' + row, { locked: true });
  const pl = await until(async () => f && await f.evaluate(() => { const c = HKPages.k.cur(), b = document.getElementById('pg-lock');
    return !!c && c.locked && !!b && !b.hidden && document.getElementById('pg-title').readOnly; }).catch(() => false), 8000);
  ok('Peek: Sperre vom anderen Geraet kommt an (Schloss, Titel schreibgeschuetzt)', !!pl);
  await other.api('PATCH', '/api/notes/' + row, { locked: false });
  const cv = (await api('GET', '/api/covers')).covers[0].url;
  await other.api('PATCH', '/api/notes/' + row, { cover: cv, cover_pos: 40 });
  const pc = await until(async () => f && await f.evaluate((u) => { const c = HKPages.k.cur(); return !!c && c.cover === u; }, cv).catch(() => false), 8000);
  ok('Peek: Titelbild vom anderen Geraet kommt an', !!pc, f && await f.evaluate(() => HKPages.k.cur().cover).catch(() => null));
  await p.keyboard.press('Escape').catch(() => {}); await sleep(800);

  ok('Keine Seitenfehler', !p.errs.length && !other.errs.length, p.errs.concat(other.errs));
  await api('DELETE', '/api/notes/' + row).catch(() => {});
  await api('DELETE', '/api/notes/' + db).catch(() => {});
  await br.close();
  const bad = res.filter((x) => !x).length;
  console.log(bad ? `${bad} FEHLER` : 'ALLES GRUEN');
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
