// Saga 2.5 P3-E3 im Web ueber Servergrenzen: Anna (Server A, Eigentuemerin) und Bert (Server B, "Bearbeiten") an
// derselben Seite - Bert oeffnet sie auf seinem Server mit negativer ID (Gesteuert von tests/browser_fed_merge.py).
//  1. verschiedene Bloecke gleichzeitig -> beide Aenderungen bleiben, kein Konflikt-Dialog, jede Seite zeigt die andere
//  2. Block-Markierung in beide Richtungen (presence2)
//  3. Ueberschneidung -> Konfliktblock bei Bert sichtbar, "Uebernehmen" ersetzt das Original beim Eigentuemer
// Umgebung: BASE_A, BASE_B, CK_A, CK_B (Cookie-Werte), NID (ID auf A), RID (negative ID auf B)
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE_A = process.env.BASE_A, BASE_B = process.env.BASE_B, NID = +process.env.NID, RID = +process.env.RID;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 9000) { const t = Date.now(); let v; while (Date.now() - t < ms) { v = await fn(); if (v) return v; await sleep(200); } return v; }
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const mk = async (base, ck, tag) => {
    const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: ck, domain: new URL(base).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = []; p.base = base;
    p.on('pageerror', (e) => p.errs.push(e.message));
    p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused|TypeError/.test(m.text())) p.errs.push(m.text()); });
    if (process.env.DEBUG) p.on('response', async (r) => { if (r.request().method() === 'PATCH') console.log('  <', tag, r.status(), (await r.text().catch(() => '')).slice(0, 200)); });
    await p.goto(base + '/notes'); return p;
  };
  const A = await mk(BASE_A, process.env.CK_A, 'A'), B = await mk(BASE_B, process.env.CK_B, 'B');
  const api = (p, m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const blocks = async () => JSON.parse((await api(A, 'GET', '/api/notes/' + NID)).body).blocks;
  const open = async (p, id) => { await p.goto(p.base + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector('.ce-block[data-id="c3"]'); await sleep(1500); };
  await open(A, NID); await open(B, RID);
  const typeEnd = async (p, bid, txt) => {
    await p.click(`.ce-block[data-id="${bid}"] .ce-paragraph`);
    await p.keyboard.press('End'); await p.keyboard.type(txt, { delay: 15 });
  };
  const textOf = (p, bid) => p.evaluate((bid) => { const e = document.querySelector(`.ce-block[data-id="${bid}"] .ce-paragraph`); return e ? e.textContent : null; }, bid);
  const markOf = (p, bid) => p.evaluate((bid) => { const b = document.querySelector(`.ce-block[data-id="${bid}"]`); return b && b.getAttribute('data-hk-by'); }, bid);

  // --- 1. verschiedene Bloecke ------------------------------------------------------------------------------------
  await Promise.all([typeEnd(A, 'a1', ' von Anna'), typeEnd(B, 'b2', ' von Bert')]);
  await sleep(4500);
  let bl = await blocks();
  ok(`1: beide Aenderungen beim Eigentuemer (${bl.map((b) => b.data.text).join(' | ')})`, bl[0].data.text === 'Erster Absatz von Anna' && bl[1].data.text === 'Zweiter Absatz von Bert', bl);
  ok('1: kein Konflikt-Dialog (A und B)', !(await A.$('#pg-conflict')) && !(await B.$('#pg-conflict')));
  const seen = await until(async () => (await textOf(A, 'b2')) === 'Zweiter Absatz von Bert' && (await textOf(B, 'a1')) === 'Erster Absatz von Anna');
  ok('1: jede Seite zeigt die Aenderung der anderen Person (ohne Neu laden)', seen, [await textOf(A, 'b2'), await textOf(B, 'a1')]);

  // --- 2. Block-Markierung ueber Servergrenzen ---------------------------------------------------------------------
  await B.click('.ce-block[data-id="c3"] .ce-paragraph'); await B.keyboard.press('End'); await B.keyboard.type('!', { delay: 20 });
  let mark = await until(() => markOf(A, 'c3'), 12000);
  ok(`2: Anna sieht, dass Bert (anderer Server) in c3 schreibt (${mark})`, mark && /Bert/.test(mark) && / bearbeitet$/.test(mark));
  ok('2: Markierung mit Farbklasse', await A.evaluate(() => /hk-by-[a-z]+/.test((document.querySelector('.ce-block[data-id="c3"]') || {}).className || '')));
  await sleep(2500);
  await A.click('.ce-block[data-id="b2"] .ce-paragraph'); await A.keyboard.press('End'); await A.keyboard.type('.', { delay: 20 });
  mark = await until(() => markOf(B, 'b2'), 12000);
  ok(`2: Bert sieht, dass Anna in b2 schreibt (${mark})`, mark && /Anna/.test(mark));
  await sleep(3000);

  // --- 3. Ueberschneidung -> Konfliktblock bei Bert ----------------------------------------------------------------
  await until(async () => (await textOf(B, 'b2')) === 'Zweiter Absatz von Bert.');
  const cur = await api(A, 'GET', '/api/notes/' + NID);
  const b1 = JSON.parse(cur.body); b1.blocks[0].data.text = 'Erster Satz (Anna)';
  await B.click('.ce-block[data-id="a1"] .ce-paragraph');
  await B.keyboard.press('End'); for (let i = 0; i < 'Absatz von Anna'.length; i++) await B.keyboard.press('Backspace');
  await B.keyboard.type('Satz (Bert)', { delay: 10 });
  await api(A, 'PATCH', '/api/notes/' + NID, { body: JSON.stringify(b1), format: 'blocks', rev: cur.rev, merge: 1 });
  await sleep(5000);
  bl = await blocks();
  ok(`3: Original = Fassung von Anna, darunter Fassung von Bert (${bl.map((b) => b.id + ':' + b.data.text).join(' | ')})`,
     bl[0].data.text === 'Erster Satz (Anna)' && bl[1].data.text === 'Erster Satz (Bert)' && bl[1].tunes && bl[1].tunes.hkconflict && bl[1].tunes.hkconflict.of === bl[0].id, bl);
  const cfVis = await until(() => B.evaluate(() => !!document.querySelector('.ce-block__content.hk-conflict .hk-cf-take')));
  ok('3: Konfliktblock bei Bert (fremde Seite) sichtbar', cfVis);
  const cfA = await until(() => A.evaluate(() => { const e = document.querySelector('.hk-conflict .hk-cf-t'); return e && e.textContent; }));
  ok(`3: Anna sieht die Fassung von Bert mit Anzeigenamen (${cfA})`, cfA && /Bert/.test(cfA) && !/fb-bert/.test(cfA));
  await B.click('.hk-conflict .hk-cf-take');
  await sleep(4000);
  bl = await blocks();
  ok(`3: Uebernehmen (bei Bert) -> beim Eigentuemer nur noch Berts Fassung (${bl.map((b) => b.data.text).join(' | ')})`,
     bl.length === 3 && bl[0].data.text === 'Erster Satz (Bert)' && !(bl[0].tunes && bl[0].tunes.hkconflict), bl);
  ok('3: Anna zeigt das Ergebnis ohne Neu laden', await until(async () => (await textOf(A, bl[0].id)) === 'Erster Satz (Bert)' && !(await A.$('.hk-conflict'))));

  ok(`Keine Fehler/CSP-Verstoesse (${A.errs.concat(B.errs).join(' / ')})`, !A.errs.length && !B.errs.length);
  await br.close();
  console.log(`\nFED-MERGE-WEB ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
