// Peek-Ansicht (Datenbank-Zeile seitlich geoeffnet) und Seitenansicht: der Seitentitel darf das Titelbild nicht ueberlappen (Bugs-DB 2253).
// Zeile mit Titelbild ohne Icon bzw. mit Icon, hell + dunkel, Desktop + schmal; in der normalen Seitenansicht ebenso
// (Titel unter dem Titelbild, Icon ragt ins Bild; Seiten mit Icon unveraendert).
// Lokal: HK_TEST_BASE=... CK=<Cookie> [SHOTS=<Ordner> SHOT=vorher|nachher] node tests/browser_peek_titel.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
if (!BASE || !CK) { console.error('HK_TEST_BASE, CK setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const NORMAL = { ohne: 12, mit: 12 };      // Abstand Titelfeld <-> Titelbild der Seitenansicht (px, Playwright-Chromium; ohne Icon vor 2253: -13)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5), SHOTS = process.env.SHOTS, SHOT = process.env.SHOT || 'nachher';
async function until(f, ms = 10000) { const t0 = Date.now(); let v; while (Date.now() - t0 < ms) { v = await f().catch(() => null); if (v) return v; await sleep(150); } return v; }

// Abstand Titel <-> Titelbild (px, > 0: Titel liegt darunter) und Icon <-> Titelbild im Dokument bzw. Rahmen fr
const measure = (fr) => fr.evaluate(() => {
  const c = document.getElementById('pg-cover'), t = document.getElementById('pg-title'), ic = document.getElementById('pg-icon');
  const rc = c.getBoundingClientRect(), rt = t.getBoundingClientRect(), ri = ic.getBoundingClientRect();
  return { cover: !c.hidden && rc.height > 0, gap: Math.round(rt.top - rc.bottom), icon: !ic.classList.contains('empty'),
    iconTop: Math.round(ri.top - rc.bottom), coverH: Math.round(rc.height), titleTop: Math.round(rt.top - rc.top) };
});
const ready = (fr, id) => until(() => fr.evaluate((id) => location.hash === '#' + id && !document.getElementById('page').hidden
  && document.getElementById('pg-title').value && !document.getElementById('pg-cover').hidden
  && document.getElementById('pg-cover-img').complete && document.getElementById('pg-cover-img').naturalWidth > 0, id));

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function session(o) {
    const ctx = await br.newContext({ viewport: o.vp, locale: 'de-DE', colorScheme: o.dark ? 'dark' : 'light' });
    await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = []; p.on('pageerror', (e) => p.errs.push(e.message));
    await p.goto(BASE + '/notes'); await sleep(300);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return p;
  }
  const P0 = await session({ vp: { width: 1440, height: 900 } });
  const covers = ((await P0.api('GET', '/api/covers')).covers || []).filter((c) => c.builtin).map((c) => c.url);
  const db = (await P0.api('POST', '/api/notes', { title: 'Rezepte ' + RUN, kind: 'database', cover: null })).id;
  await P0.api('PATCH', '/api/notes/' + db, { schema: { props: [{ id: 'mn', name: 'Minuten', type: 'number' }], views: [{ id: 'gal', name: 'Galerie', type: 'gallery' }] } });
  const plain = (await P0.api('POST', '/api/notes', { title: 'Apfelkuchen nach Oma Lena', parent_id: db })).id;
  const withIc = (await P0.api('POST', '/api/notes', { title: 'Tomatensuppe', parent_id: db })).id;
  await P0.api('PATCH', '/api/notes/' + plain, { cover: covers[0] });
  await P0.api('PATCH', '/api/notes/' + withIc, { cover: covers[1] || covers[0], icon: '🍅' });
  ok('Datenbank mit zwei Zeilen (Titelbild ohne / mit Icon)', db && plain && withIc && covers.length, { db, plain, withIc, n: covers.length });
  await P0.context().close();

  const CASES = [{ n: 'desktop-hell', vp: { width: 1440, height: 900 } }, { n: 'desktop-dunkel', vp: { width: 1440, height: 900 }, dark: true },
    { n: 'schmal-hell', vp: { width: 700, height: 900 } }, { n: 'schmal-dunkel', vp: { width: 700, height: 900 }, dark: true }];
  const normal = {};
  for (const cs of CASES) {
    const p = await session(cs);
    // normale Seitenansicht (Referenz, darf sich nicht aendern)
    for (const [k, id] of [['ohne Icon', plain], ['mit Icon', withIc]]) {
      await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
      ok(`${cs.n}: Seite ${k} geladen`, await ready(p, id));
      const m = await measure(p); normal[cs.n + k] = m;
      // Seitenansicht: ohne Icon wie im Peek knapp unter dem Bild (vor 2253: 13 px im Bild), mit Icon unveraendert
      const want = k === 'ohne Icon' ? NORMAL.ohne : NORMAL.mit;
      ok(`${cs.n}: normale Ansicht ${k}: Titel ${m.gap} px unter dem Titelbild (erwartet ${want})`, m.cover && Math.abs(m.gap - want) <= 1, m);
      if (k === 'mit Icon') ok(`${cs.n}: normale Ansicht: Icon ragt ins Titelbild (${m.iconTop} px)`, m.icon && m.iconTop < 0, m);
      if (SHOTS && k === 'ohne Icon') await p.screenshot({ path: `${SHOTS}/seite2-${SHOT}-${cs.n}.png` }).catch(e => console.log('--   Bild: ' + e.message.split('\n')[0]));
    }
    // Peek: Datenbank (Galerie) -> Karte anklicken
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + db);
    await until(() => p.$('.dbg-card[data-id="' + plain + '"]'));
    for (const [k, id] of [['ohne Icon', plain], ['mit Icon', withIc]]) {
      await p.click('.dbg-card[data-id="' + id + '"]');
      const fh = await until(async () => { const v = await p.$('#pg-peek:not([hidden]) #peek-frame'); return v; });
      const fr = fh && await fh.contentFrame();
      const up = fr && await ready(fr, id);
      ok(`${cs.n}: Peek ${k} offen`, !!up, { peek: !!fh });
      if (!up) continue;
      await sleep(400);
      const m = await measure(fr);
      ok(`${cs.n}: Peek ${k}: Titel ueberlappt das Titelbild nicht (Abstand ${m.gap} px, mind. 8)`, m.cover && m.gap >= 8, m);
      if (m.icon) ok(`${cs.n}: Peek ${k}: Icon ragt weiter ins Titelbild (${m.iconTop} px)`, m.iconTop < 0, m);
      if (SHOTS && k === 'ohne Icon') await p.screenshot({ path: `${SHOTS}/peek-${SHOT}-${cs.n}.png` }).catch(e => console.log('--   Bild: ' + e.message.split('\n')[0]));
      await p.click('#peek-close'); await until(() => p.evaluate(() => document.getElementById('pg-peek').hidden));
    }
    ok(`${cs.n}: keine Skriptfehler`, !p.errs.length, p.errs.slice(0, 3));
    await p.context().close();
  }
  console.log('MESS ' + JSON.stringify(normal));
  const P1 = await session({ vp: { width: 1440, height: 900 } });
  await P1.api('DELETE', '/api/notes/' + db);
  await br.close();
  console.log(`\nBROWSER-PEEK-TITEL ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
