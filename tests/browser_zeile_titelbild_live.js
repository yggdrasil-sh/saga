// Live: Titelbild einer Datenbank-Zeile aendert die Galerie-Karte anderer Browser ohne Neuladen (Bug 2122).
// Vorher meldete der Server ein neues Titelbild nicht an die Datenbank (announce_row nur bei props/title/icon) und das
// Web hoerte nicht auf db.rows - eingebettete Datenbank-Bloecke auf anderen Seiten luden auch sonst nie live nach.
// Browser: P = Datenbank-Seite (Galerie, Konto A), V = Konto B mit Freigabe "Lesen" auf der Datenbank (Galerie), E = Konto C
// mit Freigabe auf Datenbank und Seite: andere Seite mit eingebettetem Datenbank-Block (Galerie) - eigenes Konto, damit
// die Anwesenheit von P/V (je Konto) das Ereignis nicht mitbringt. Ein viertes "Geraet" setzt Titelbild (Galerie-Bild, anderes Bild,
// entfernen) per API -> alle drei Karten zeigen das neue Bild bzw. den Platzhalter, ohne Neuladen.
// Lokal: HK_TEST_BASE=... CK=<Cookie A> CK2=<Cookie B> U2=<Konto B> CK3=<Cookie C> U3=<Konto C> [SHOTS=<Ordner> SHOT=vorher|nachher]
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2, U2 = process.env.U2, CK3 = process.env.CK3, U3 = process.env.U3;
if (!BASE || !CK || !CK2 || !U2 || !CK3 || !U3) { console.error('HK_TEST_BASE, CK, CK2, U2, CK3, U3 setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);
const SHOTS = process.env.SHOTS, SHOT = process.env.SHOT || 'nachher';

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function session(ck) {
    const ctx = await br.newContext({ viewport: { width: 1100, height: 760 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: ck, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = []; p.nav = 0;
    p.on('pageerror', (e) => p.errs.push(e.message));
    p.on('framenavigated', (f) => { if (f === p.mainFrame()) p.nav++; });
    await p.addInitScript(() => { document.addEventListener('hk-live', (e) => { if (e.detail && e.detail.type === 'live.hello') window.__hello = true; }); });
    await p.goto(BASE + '/notes'); await sleep(300);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return p;
  }
  const P = await session(CK), E = await session(CK3), V = await session(CK2), dev = await session(CK);
  const api = dev.api;
  const covers = ((await api('GET', '/api/covers')).covers || []).filter((c) => c.builtin).map((c) => c.url);
  const db = (await api('POST', '/api/notes', { title: 'Galerie ' + RUN, kind: 'database', cover: null })).id;
  await api('PATCH', '/api/notes/' + db, { schema: { props: [], views: [{ id: 'gal', name: 'Galerie', type: 'gallery' }] } });
  const row = (await api('POST', '/api/notes', { title: 'Karte A', parent_id: db })).id;
  await api('POST', '/api/notes', { title: 'Karte B', parent_id: db });
  await api('PATCH', '/api/notes/' + row, { cover: covers[0] });
  const host = (await api('POST', '/api/notes', { title: 'Mit Galerie ' + RUN, cover: null })).id;
  await api('PATCH', '/api/notes/' + host, { format: 'blocks', body: JSON.stringify({ blocks: [
    { id: 'h0', type: 'paragraph', data: { text: 'Eingebettet:' } }, { id: 'h1', type: 'database', data: { db: db, view: null, views: null } }] }) });
  await api('PUT', '/api/notes/' + db + '/shares', { type: 'user', target: U2, role: 'view' });
  await api('PUT', '/api/notes/' + db + '/shares', { type: 'user', target: U3, role: 'view' });
  await api('PUT', '/api/notes/' + host + '/shares', { type: 'user', target: U3, role: 'view' });

  const card = (p) => p.evaluate((id) => {
    const c = document.querySelector('.dbg-card[data-id="' + id + '"]'); if (!c) return null;
    const im = c.querySelector('.dbg-prev img'); return { img: im ? im.getAttribute('src') : null, ph: !!c.querySelector('.dbg-ph') };
  }, row);
  async function open(p, id) {
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
    for (let i = 0; i < 80; i++) { if (await card(p)) break; await sleep(150); }
    for (let i = 0; i < 40; i++) { if (await p.evaluate(() => !!window.__hello)) break; await sleep(250); }
    await sleep(800); p.nav = 0;
  }
  async function until(fn, ms = 9000) { const t0 = Date.now(); let v; while (Date.now() - t0 < ms) { v = await fn(); if (v) return v; await sleep(200); } return v; }
  await open(P, db); await open(E, host); await open(V, db);
  const views = [['Datenbank-Seite', P], ['eingebetteter Block', E], ['Freigabe Lesen', V]];
  for (const [n, p] of views) { const c = await card(p); ok(n + ': Karte zeigt das erste Titelbild', c && c.img === covers[0], c); }
  async function shot(p, name) {
    if (!SHOTS) return;
    const c = await p.$('.dbg-card[data-id="' + row + '"]'); const g = c && await c.evaluateHandle((x) => x.closest('.dbg') || x);
    await (g || p).screenshot({ path: SHOTS + '/' + SHOT + '-' + name + '.png' }).catch(() => {});
  }
  await shot(V, '2122-karte-vor-aenderung');

  for (const [step, body, want] of [['anderes Galerie-Bild', { cover: covers[1], cover_pos: 30 }, covers[1]],
                                    ['drittes Bild', { cover: covers[2] }, covers[2]],
                                    ['Titelbild entfernt', { cover: null }, null]]) {
    await api('PATCH', '/api/notes/' + row, body);
    for (const [n, p] of views) {
      const c = await until(async () => { const c = await card(p); return c && (want ? c.img === want : !c.img && c.ph) ? c : null; }) || await card(p);
      ok(n + ': ' + step + ' kommt ohne Neuladen an', c && (want ? c.img === want : !c.img && c.ph) && p.nav === 0, { c, want, nav: p.nav });
    }
    if (step === 'anderes Galerie-Bild') await shot(V, '2122-karte-nach-aenderung');
  }

  ok('Keine Seitenfehler', ![P, E, V, dev].some((p) => p.errs.length), [P, E, V, dev].map((p) => p.errs));
  for (const id of [host, db]) await api('DELETE', '/api/notes/' + id).catch(() => {});
  await br.close();
  const bad = res.filter((x) => !x).length;
  console.log(bad ? `${bad} FEHLER` : 'ALLES GRUEN');
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
