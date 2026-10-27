// Saga 2.5 P3 Stufe 1 im Web: zwei Personen (A Eigentuemer, B mit "Bearbeiten") an derselben Seite.
//  1. verschiedene Bloecke gleichzeitig -> beide Aenderungen bleiben, kein Konflikt-Dialog
//  2. gleicher Absatz ohne Ueberschneidung -> zusammengefuehrt
//  3. gleicher Absatz mit Ueberschneidung -> Konfliktblock (gelb) direkt darunter, "Uebernehmen" ersetzt das Original
//  4. Weiterschreiben, waehrend das Speichern langsam ist (Antwort verzoegert) -> nichts geht verloren, nichts doppelt
//  5. Block-Markierung: A sieht den Block, in dem B schreibt
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> CK2=<Cookie hk-test-b> node tests/merge_web.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 8000) { const t = Date.now(); let v; while (Date.now() - t < ms) { v = await fn(); if (v) return v; await sleep(150); } return v; }
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const mk = async (ck) => {
    const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: ck, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = [];
    p.on('pageerror', (e) => p.errs.push(e.message));
    p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused|TypeError/.test(m.text())) p.errs.push(m.text()); });
    if (process.env.DEBUG) p.on('request', (r) => { if (r.method() === 'PATCH') console.log('  >', ck === CK ? 'A' : 'B', (r.postData() || '').replace(/"body":"[^]*?","format"/, '"body":…,"format"').slice(0, 200)); });
    if (process.env.DEBUG) p.on('response', async (r) => { if (r.request().method() === 'PATCH') console.log('  <', ck === CK ? 'A' : 'B', r.status(), (await r.text().catch(() => '')).slice(0, 160)); });
    await p.goto(BASE + '/notes'); return p;
  };
  const A = await mk(CK), B = await mk(CK2);
  const api = (p, m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const P = (id, t) => ({ id, type: 'paragraph', data: { text: t } });
  const id = (await api(A, 'POST', '/api/notes', { title: 'Gemeinsam' })).id;
  await api(A, 'PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks: [P('a1', 'Erster Absatz'), P('b2', 'Zweiter Absatz'), P('c3', 'Hallo Welt')] }), format: 'blocks' });
  const sh = await api(A, 'PUT', '/api/notes/' + id + '/shares', { type: 'user', target: 'hk-test-b', role: 'edit' });
  ok('Freigabe an B (Bearbeiten)', !sh.error, sh);
  const blocks = async () => JSON.parse((await api(A, 'GET', '/api/notes/' + id)).body).blocks;
  const open = async (p) => { await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector('.ce-block[data-id="c3"]'); await sleep(1500); };
  await open(A); await open(B);
  const typeEnd = async (p, bid, txt) => {
    await p.click(`.ce-block[data-id="${bid}"] .ce-paragraph`);
    await p.keyboard.press('End'); await p.keyboard.type(txt, { delay: 15 });
  };
  const textOf = (p, bid) => p.evaluate((bid) => { const e = document.querySelector(`.ce-block[data-id="${bid}"] .ce-paragraph`); return e ? e.textContent : null; }, bid);

  // --- 1. verschiedene Bloecke ------------------------------------------------------------------------------------
  await Promise.all([typeEnd(A, 'a1', ' von A'), typeEnd(B, 'b2', ' von B')]);
  await sleep(3500);
  let bl = await blocks();
  ok(`1: beide Aenderungen gespeichert (${bl.map((b) => b.data.text).join(' | ')})`, bl[0].data.text === 'Erster Absatz von A' && bl[1].data.text === 'Zweiter Absatz von B');
  const bar = await A.$('#pg-conflict') || await B.$('#pg-conflict');
  ok('1: kein Konflikt-Dialog', !bar);
  const seen = await until(async () => (await textOf(A, 'b2')) === 'Zweiter Absatz von B' && (await textOf(B, 'a1')) === 'Erster Absatz von A');
  ok('1: jede Seite zeigt die Aenderung der anderen Person (ohne Neu laden)', seen, [await textOf(A, 'b2'), await textOf(B, 'a1')]);

  // --- 5. Block-Markierung (B schreibt in c3) ----------------------------------------------------------------------
  await B.click('.ce-block[data-id="c3"] .ce-paragraph'); await B.keyboard.press('End'); await B.keyboard.type('!', { delay: 20 });
  const mark = await until(() => A.evaluate(() => { const b = document.querySelector('.ce-block[data-id="c3"]'); return b && b.getAttribute('data-hk-by'); }), 9000);
  ok(`5: A sieht, dass B in Block c3 schreibt (${mark})`, mark && / bearbeitet$/.test(mark));
  await sleep(2500);

  // --- 2. gleicher Absatz ohne Ueberschneidung ---------------------------------------------------------------------
  await until(async () => (await textOf(A, 'c3')) === 'Hallo Welt!');
  await A.click('.ce-block[data-id="c3"] .ce-paragraph'); await A.keyboard.press('Home'); await A.keyboard.type('Ja, ', { delay: 15 });
  await B.click('.ce-block[data-id="c3"] .ce-paragraph'); await B.keyboard.press('End'); await B.keyboard.type(' Wie gehts?', { delay: 15 });
  await sleep(4000);
  bl = await blocks();
  ok(`2: zusammengefuehrt (${bl[2].data.text})`, bl[2].data.text === 'Ja, Hallo Welt! Wie gehts?' && bl.length === 3);
  ok('2: beide sehen das Ergebnis', await until(async () => (await textOf(A, 'c3')) === 'Ja, Hallo Welt! Wie gehts?' && (await textOf(B, 'c3')) === 'Ja, Hallo Welt! Wie gehts?'),
     [await textOf(A, 'c3'), await textOf(B, 'c3')]);

  // --- 3. Ueberschneidung -> Konfliktblock ------------------------------------------------------------------------
  // B speichert direkt ueber die API (wie eine zweite Person, deren Speichern gerade zuerst ankam)
  const cur = await api(B, 'GET', '/api/notes/' + id);
  const b1 = JSON.parse(cur.body); b1.blocks[0].data.text = 'Erster Satz (B)';
  // A tippt in denselben Absatz, ohne die Aenderung von B zu kennen (Live-Ereignis kommt erst danach)
  await A.click('.ce-block[data-id="a1"] .ce-paragraph');
  await A.keyboard.press('End'); for (let i = 0; i < 'Absatz von A'.length; i++) await A.keyboard.press('Backspace');
  await A.keyboard.type('Satz (A)', { delay: 10 });
  await api(B, 'PATCH', '/api/notes/' + id, { body: JSON.stringify(b1), format: 'blocks', rev: cur.rev, merge: 1 });
  await sleep(4000);
  bl = await blocks();
  ok(`3: Original = Fassung von B, darunter Konfliktblock mit Fassung von A (${bl.map((b) => b.id + ':' + b.data.text).join(' | ')})`,
     bl[0].data.text === 'Erster Satz (B)' && bl[1].data.text === 'Erster Satz (A)' && bl[1].tunes && bl[1].tunes.hkconflict && bl[1].tunes.hkconflict.of === bl[0].id, bl);
  const cfVis = await until(() => A.evaluate(() => !!document.querySelector('.ce-block__content.hk-conflict .hk-cf-take')));
  ok('3: Konfliktblock im Web sichtbar (gelb, Uebernehmen/Verwerfen)', cfVis);
  const vers = await api(A, 'GET', '/api/notes/' + id + '/versions');
  ok('3: vorher Version gesichert', Array.isArray(vers.versions || vers) && (vers.versions || vers).length >= 1, vers);
  await A.click('.hk-conflict .hk-cf-take');
  await sleep(3500);
  bl = await blocks();
  ok(`3: Uebernehmen -> nur noch die Fassung von A, ohne Marke (${bl.map((b) => b.data.text).join(' | ')})`,
     bl.length === 3 && bl[0].data.text === 'Erster Satz (A)' && !(bl[0].tunes && bl[0].tunes.hkconflict), bl);

  // --- 4. Weiterschreiben waehrend langsamer Antwort -------------------------------------------------------------
  await A.route('**/api/notes/' + id, async (route) => {
    if (route.request().method() === 'PATCH') { await sleep(2500); }
    await route.continue();
  });
  await typeEnd(A, 'b2', ' eins');
  await sleep(1100);                               // erstes Speichern laeuft (verzoegert)
  await A.keyboard.type(' zwei', { delay: 15 });   // weitergeschrieben, waehrend die Antwort unterwegs ist
  await typeEnd(B, 'c3', ' (B)');                  // gleichzeitig fremde Aenderung in einem anderen Block
  await sleep(9000);
  await A.unroute('**/api/notes/' + id);
  bl = await blocks();
  ok(`4: nichts verloren, nichts doppelt (${bl.map((b) => b.data.text).join(' | ')})`,
     bl[1].data.text === 'Zweiter Absatz von B eins zwei' && bl[2].data.text === 'Ja, Hallo Welt! Wie gehts? (B)' && bl.length === 3, bl);
  ok('4: A zeigt den Stand des Servers', await until(async () => (await textOf(A, 'c3')) === 'Ja, Hallo Welt! Wie gehts? (B)'), await textOf(A, 'c3'));

  ok(`Keine Fehler/CSP-Verstoesse (${A.errs.concat(B.errs).join(' / ')})`, !A.errs.length && !B.errs.length);
  await api(A, 'DELETE', '/api/notes/' + id);
  await br.close();
  console.log(`\nMERGE-WEB ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
