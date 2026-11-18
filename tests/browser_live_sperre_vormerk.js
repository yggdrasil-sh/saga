// Live-Bearbeitung (notes.live an): Sperre verpasst -> Eingabe vormerken statt verwerfen (Bug 2123).
// Vorher: pm-live bekam beim Tippen reject readonly, baute den Editor aus dem Server-Stand neu (getippter Text weg) und
// zeigte nur "Änderung nicht übernommen"; Schloss/Sperre erschienen erst beim naechsten 423 auf den Titel.
// Die Sperre wird direkt in der Datenbank gesetzt (kein Ereignis an den Browser = verpasst).
// 1) Eigentuemer tippt -> Schloss + Hinweis, Text bleibt sichtbar und vorgemerkt (nicht beim Server), Neu laden fragt nach
//    -> Klick aufs Schloss -> Text gespeichert; danach weiter bearbeitbar (live).
// 2) Wie 1, aber ein anderes Geraet entsperrt -> Text gespeichert, Schloss weg.
// 3) Rechte entzogen (Freigabe Bearbeiten -> Lesen, ebenfalls ohne Ereignis): nichts vormerken, kein Schloss, klarer
//    Hinweis "nur noch lesen", Seite danach schreibgeschuetzt.
// Lokal: HK_TEST_BASE=... CK=<Cookie A> CK2=<Cookie B> U2=<Konto B> DATABASE_URL=... [SHOTS=<Ordner> SHOT=vorher|nachher]
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const { execFileSync } = require('child_process');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2, U2 = process.env.U2, DBURL = process.env.DATABASE_URL;
if (!BASE || !CK || !CK2 || !U2 || !DBURL) { console.error('HK_TEST_BASE, CK, CK2, U2, DATABASE_URL setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);
const SHOTS = process.env.SHOTS, SHOT = process.env.SHOT || 'nachher';
const psql = (q) => execFileSync('psql', [DBURL, '-qc', q]);
const lockDb = (id, on) => psql(`UPDATE note SET locked=${on ? 1 : 0} WHERE id=${id}`);

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function session(ck) {
    const ctx = await br.newContext({ viewport: { width: 1180, height: 800 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: ck, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = [];
    p.on('pageerror', (e) => p.errs.push(e.message));
    await p.addInitScript(() => { document.addEventListener('hk-live', (e) => { if (e.detail && e.detail.type === 'live.hello') window.__hello = true; }); });
    await p.goto(BASE + '/notes'); await sleep(300);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return p;
  }
  const p = await session(CK), other = await session(CK), p2 = await session(CK2);
  const api = other.api;
  const mk = async (t) => {
    const id = (await api('POST', '/api/notes', { title: t + ' ' + RUN, cover: null })).id;
    await api('PATCH', '/api/notes/' + id, { format: 'blocks', body: JSON.stringify({ blocks: [{ id: 'a1', type: 'paragraph', data: { text: 'Start' } }] }) });
    return id;
  };
  const pg1 = await mk('Vormerk live'), pg2 = await mk('Vormerk fremd'), pg3 = await mk('Rechte');
  await api('PUT', '/api/notes/' + pg3 + '/shares', { type: 'user', target: U2, role: 'edit' });

  const st = (q) => q.evaluate(() => {
    const c = HKPages.k.cur(), b = document.getElementById('pg-lock'), s = document.getElementById('pg-state'), L = window.HKPmLive ? HKPmLive.state() : {};
    return { locked: !!(c && c.locked), icon: !!b && !b.hidden, state: s ? s.textContent : '', ro: !!HKPages.k.readOnly(),
             body: (document.getElementById('pg-editor') || {}).textContent || '', live: !!L.live, pmRo: !!L.ro, vorm: !!L.vorm,
             toasts: (window.__toasts || []).slice() };
  });
  const srv = async (id) => (await api('GET', '/api/notes/' + id)).body || '';
  async function until(fn, ms = 8000) { const t0 = Date.now(); let v; while (Date.now() - t0 < ms) { v = await fn(); if (v) return v; await sleep(200); } return v; }
  async function open(q, id) {
    lockDb(id, false);
    await q.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
    for (let i = 0; i < 60; i++) { if (await q.evaluate((id) => !!(HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector('#pg-editor [contenteditable="true"]')), id)) break; await sleep(100); }
    for (let i = 0; i < 40; i++) { if ((await st(q)).live) break; await sleep(250); }
    for (let i = 0; i < 40; i++) { if (await q.evaluate(() => !!window.__hello)) break; await sleep(250); }
    await q.evaluate(() => { window.__toasts = []; const o = HK.toast; HK.toast = function (m) { window.__toasts.push(String(m)); return o.apply(this, arguments); }; });
    await sleep(600);
  }
  // fast: ein Wort in einem Rutsch (eine Transaktion = ein Update; was nach der Ablehnung kommt, nimmt der dann
  // schreibgeschuetzte Editor nicht mehr an - wie das Titelfeld nach 423)
  async function typeEnd(q, txt, fast) {
    await q.click('#pg-editor [contenteditable="true"] p', { timeout: 4000 }).catch(() => {});      // Basis: Editor bleibt schreibgeschuetzt
    await q.keyboard.press('End');
    if (fast) await q.keyboard.insertText(txt); else await q.keyboard.type(txt, { delay: 35 });
  }
  const oldToast = (s) => s.toasts.some((t) => /nicht übernommen/.test(t));

  // --- 1. Eigentuemer, Sperre verpasst, selbst entsperren --------------------------------------------------------
  await open(p, pg1);
  const s0 = await st(p);
  console.log('     live gebunden: ' + s0.live);
  if (!s0.live && !(((await api('GET', '/api/version')).features || []).includes('live1'))) {
    console.log('     Hinweis: notes.live ist aus - diese Reihe braucht den Schalter an (ohne live: tests/browser_sperre_vormerk.js)');
    for (const id of [pg1, pg2, pg3]) await api('DELETE', '/api/notes/' + id).catch(() => {});
    await br.close(); console.log('ALLES GRUEN (uebersprungen)'); process.exit(0);
  }
  ok('Ausgang: live gebunden, nicht gesperrt', s0.live && !s0.locked && !s0.pmRo, s0);
  lockDb(pg1, true);
  await typeEnd(p, ' Vorgemerkt01', true);
  const s1 = await until(async () => { const s = await st(p); return s.locked && s.icon ? s : null; }) || await st(p);
  ok('Sperre verpasst: Tippen zeigt das Schloss', s1.locked && s1.icon && s1.ro, s1);
  ok('Hinweis nennt die Sperre', /gesperrt/i.test(s1.state), s1.state);
  await sleep(2000);
  const s1b = await st(p);
  ok('Getippter Text bleibt sichtbar', /Vorgemerkt01/.test(s1b.body), s1b.body);
  ok('Eingabe vorgemerkt (pm-live), nicht gesendet', s1b.vorm && s1b.pmRo, s1b);
  ok('Kein "Änderung nicht übernommen" (Eingabe nicht verworfen)', !oldToast(s1b), s1b.toasts);
  ok('Server hat den Text (noch) nicht', !/Vorgemerkt01/.test(await srv(pg1)), await srv(pg1));
  await p.keyboard.type('xy', { delay: 30 }); await sleep(500);
  const s1d = await st(p);
  ok('Weitertippen bei Sperre: Editor nimmt nichts an, Vormerkung bleibt', /Vorgemerkt01$/.test(s1d.body.replace(/[+⋮\s]+$/, '')) && s1d.vorm, s1d.body);
  const asks = await p.evaluate(() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; });
  ok('Neu laden mit Vormerkung: Browser fragt nach', asks, asks);
  if (SHOTS) await p.screenshot({ path: SHOTS + '/' + SHOT + '-2123-schloss-vorgemerkt.png' });
  if (s1b.icon) await p.click('#pg-lock', { timeout: 3000 });
  const n1 = await until(async () => /Vorgemerkt01/.test(await srv(pg1)), 12000);
  ok('Nach dem Entsperren (Schloss) gespeichert', !!n1, await srv(pg1));
  const s1c = await until(async () => { const s = await st(p); return !s.locked && !s.icon && !s.pmRo && !s.vorm ? s : null; }) || await st(p);
  ok('Danach: Schloss weg, live schreibbar, nichts mehr vorgemerkt, kein Fehlerstatus', !s1c.locked && !s1c.icon && !s1c.pmRo && !s1c.vorm && !/Nicht gespeichert/.test(s1c.state), s1c);
  if (SHOTS) await p.screenshot({ path: SHOTS + '/' + SHOT + '-2123-nach-entsperren.png' });
  await typeEnd(p, ' Danach01');
  ok('Danach weiter bearbeitbar (live gespeichert)', !!await until(async () => /Danach01/.test(await srv(pg1)), 12000), await srv(pg1));
  const asks2 = await p.evaluate(() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; });
  ok('Ohne Vormerkung: keine Nachfrage', !asks2, asks2);

  // --- 2. Anderes Geraet entsperrt --------------------------------------------------------------------------------
  await open(p, pg2);
  lockDb(pg2, true);
  await typeEnd(p, ' Vorgemerkt02', true);
  const s2 = await until(async () => { const s = await st(p); return s.locked && s.vorm ? s : null; }) || await st(p);
  ok('Fremd: Schloss + vorgemerkt', s2.locked && s2.vorm && /Vorgemerkt02/.test(s2.body), s2);
  await other.api('PATCH', '/api/notes/' + pg2, { locked: false });
  ok('Fremd entsperrt: Text gespeichert', !!await until(async () => /Vorgemerkt02/.test(await srv(pg2)), 12000), await srv(pg2));
  const s2b = await until(async () => { const s = await st(p); return !s.locked && !s.icon && !s.pmRo ? s : null; }) || await st(p);
  ok('Fremd entsperrt: Schloss weg, schreibbar', !s2b.locked && !s2b.icon && !s2b.pmRo && !s2b.vorm, s2b);

  // --- 3. Rechte entzogen (ohne Ereignis) ------------------------------------------------------------------------
  await open(p2, pg3);
  const s3a = await st(p2);
  ok('Freigabe Bearbeiten: live gebunden, schreibbar', s3a.live && !s3a.pmRo && !s3a.ro, s3a);
  psql(`UPDATE note_share SET role='view' WHERE note_id=${pg3}`);
  await typeEnd(p2, ' Rolle03', true);
  const s3 = await until(async () => { const s = await st(p2); return s.toasts.length ? s : null; }) || await st(p2);
  ok('Rechte entzogen: Hinweis "nur noch lesen"', s3.toasts.some((t) => /nur noch lesen/.test(t)), s3.toasts);
  ok('Rechte entzogen: kein Schloss, nichts vorgemerkt', !s3.locked && !s3.icon && !s3.vorm, s3);
  const s3b = await until(async () => { const s = await st(p2); return s.ro ? s : null; }) || await st(p2);
  ok('Rechte entzogen: Seite danach schreibgeschuetzt (Rolle neu geprueft)', s3b.ro, s3b);
  await sleep(1500);
  ok('Rechte entzogen: Server hat den Text nicht', !/Rolle03/.test(await srv(pg3)), await srv(pg3));

  ok('Keine Seitenfehler', ![p, other, p2].some((q) => q.errs.length), [p, other, p2].map((q) => q.errs));
  for (const id of [pg1, pg2, pg3]) { lockDb(id, false); await api('DELETE', '/api/notes/' + id).catch(() => {}); }
  await br.close();
  const bad = res.filter((x) => !x).length;
  console.log(bad ? `${bad} FEHLER` : 'ALLES GRUEN');
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
