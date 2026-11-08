// Eigene Aenderung nicht als fremd melden (2.5, Fund: "<Name> hat diese Seite geaendert" im selben Fenster). Seite mit
// eingebetteter Datenbank (views: null, Status-Auswahl): erst im Text tippen, dann sofort in eine Zelle (Titel der Zeile)
// und den Fokus dort lassen, 3,5 s warten -> kein #pg-remote, Zelle und Text gespeichert; nur Oeffnen + Zelle -> Revision
// der Seite bleibt. Zweite Sitzung desselben Kontos aendert den Seitentext, waehrend der Fokus in der Zelle steht:
// live gebunden (notes.live, Tiptap) still uebernommen, sonst Hinweis "Neu laden" wie bisher. Zeile als Seite offen,
// zweite Sitzung aendert nur eine Eigenschaft -> kein Hinweis; ohne Fokus in den Eigenschaften still nachgezogen.
// Laeuft in beiden Modi (SAGA_EDITOR=editorjs|tiptap; stellt die Konto-Wahl selbst ein, wenn der Admin choice erlaubt).
// Lokal: HK_TEST_BASE=... CK=<Cookie hk-test-a> [SAGA_EDITOR=tiptap] node tests/browser_eigene_aenderung.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
if (!BASE || !CK) { console.error('HK_TEST_BASE, CK setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function session() {
    const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = [];
    p.on('pageerror', (e) => p.errs.push(e.message));
    await p.goto(BASE + '/notes'); await sleep(400);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return { ctx, p };
  }
  const { p } = await session(), api = p.api;
  const was = await api('GET', '/api/account/editor');
  if (was.choice) { await D.setEditor(p, D.EDITOR); await p.reload(); await sleep(400); }
  const pg = (await api('POST', '/api/notes', { title: 'Eigene Aenderung Seite' })).id;
  const db = (await api('POST', '/api/notes', { title: 'Eigene Aenderung DB', kind: 'database', parent_id: pg })).id;
  await api('PATCH', '/api/notes/' + db, { schema: { props: [{ id: 'st', name: 'Status', type: 'select', options: [{ id: 'of', name: 'Offen', color: 'gray' }, { id: 'oa', name: 'In Arbeit', color: 'blue' }, { id: 'fe', name: 'Fertig', color: 'green' }] },
                                                             { id: 'nz', name: 'Notiz', type: 'text' }] } });
  const row = (await api('POST', '/api/notes', { title: 'Zeile 1', parent_id: db })).id;
  await api('PATCH', '/api/notes/' + row, { props: { st: 'of' } });
  await api('PATCH', '/api/notes/' + pg, { format: 'blocks', body: JSON.stringify({ blocks: [
    { id: 'o0', type: 'paragraph', data: { text: 'Oben' } },
    { id: 'o1', type: 'database', data: { db, view: null, views: null } },
    { id: 'o2', type: 'paragraph', data: { text: 'Unten' } }] }) });
  const cell = D.block('o1') + ' tr.db-row textarea.db-title-in';
  async function openPg() {
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + pg);
    await p.waitForSelector(cell, { timeout: 15000 }).catch(() => null);
    for (let i = 0; i < 20; i++) { if (await p.evaluate(() => !!(HKPages.k.liveBound && HKPages.k.liveBound()))) break; await sleep(250); }
  }
  const banner = () => p.evaluate(() => { const b = document.getElementById('pg-remote'); return b ? b.textContent : null; });

  // --- 1. Nur oeffnen + Zelle bearbeiten: kein Hinweis, Revision der Seite bleibt -----------------------------------
  await openPg();
  const live = await p.evaluate(() => !!(HKPages.k.liveBound && HKPages.k.liveBound()));
  console.log('     Editor ' + (await D.isEditor(p)) + ', live gebunden: ' + live);
  ok('Editor wie erwartet (' + D.EDITOR + ')', (await D.isEditor(p)) === D.EDITOR, await D.isEditor(p));
  const r0 = (await api('GET', '/api/notes/' + pg)).rev;
  await p.click(cell); await p.keyboard.press('End'); await p.keyboard.type(' a', { delay: 30 });
  await sleep(3500);
  ok('Zelle bearbeitet, Fokus bleibt in der Zelle', await p.evaluate((s) => document.activeElement === document.querySelector(s), cell));
  ok('Kein Hinweis "geändert" (nur Zelle)', (await banner()) === null, await banner());
  const r1 = (await api('GET', '/api/notes/' + pg)).rev;
  ok(`Revision der Seite bleibt (${r0} -> ${r1})`, r1 === r0);

  // --- 2. Text tippen, sofort in die Zelle: das eigene Speichern/Materialisieren meldet sich nicht als fremd ------------
  await D.typeEnd(p, 'o2', ' eins');
  await p.click(cell); await p.keyboard.press('End'); await p.keyboard.type(' b', { delay: 30 });
  await sleep(3500);
  ok('Kein Hinweis nach eigenem Text + Zelle', (await banner()) === null, await banner());
  const b2 = JSON.parse((await api('GET', '/api/notes/' + pg)).body).blocks;
  ok('Text gespeichert', b2[2] && b2[2].data.text === 'Unten eins', b2[2]);

  // --- 3. Zweite Sitzung desselben Kontos aendert den Seitentext, Fokus steht in der Zelle ----------------------------
  const s2 = await session();
  const cur = await s2.p.api('GET', '/api/notes/' + pg), bl = JSON.parse(cur.body).blocks;
  bl[0].data.text = 'Oben von woanders';
  const pr = await s2.p.api('PATCH', '/api/notes/' + pg, { format: 'blocks', body: JSON.stringify({ blocks: bl }), rev: cur.rev, merge: 1 });
  ok('Zweite Sitzung: gespeichert', pr && pr.rev > cur.rev, pr);
  await sleep(3000);
  const txt0 = await p.evaluate((s) => { const b = document.querySelector(s); return b ? b.textContent : null; }, D.block('o0'));
  if (live) {
    ok('Live: fremder Text still übernommen', /Oben von woanders/.test(txt0 || ''), txt0);
    ok('Live: kein Hinweis "Neu laden"', (await banner()) === null, await banner());
  } else {
    const bn = await banner();
    ok('Ohne Live: Hinweis wie bisher (Fokus in der Zelle)', !!bn && /geändert/.test(bn), bn);
  }
  ok('Fokus bleibt in der Zelle', await p.evaluate((s) => document.activeElement === document.querySelector(s), cell));
  await p.keyboard.press('Tab'); await sleep(1200);             // Zelle verlassen: Titel der Zeile wird gespeichert
  const rt = (await api('GET', '/api/notes/' + row)).title;
  ok(`Zeile gespeichert (${rt})`, rt === 'Zeile 1 a b', rt);

  // --- 4. Zeile als Seite offen: zweite Sitzung aendert nur eine Eigenschaft -> kein Hinweis --------------------------
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + row);
  await p.waitForSelector('#pg-props .prop-line', { timeout: 15000 }).catch(() => null);
  await sleep(2500);                                            // Anwesenheit gemeldet (sonst kein note.changed)
  await p.focus('#pg-title');                                   // Eingabe ausserhalb des Editors (wie Zelle)
  await s2.p.api('PATCH', '/api/notes/' + row, { props: { st: 'oa' } });
  await sleep(3000);
  ok('Nur Eigenschaft geändert: kein Hinweis', (await banner()) === null, await banner());
  const pv = await p.evaluate(() => document.getElementById('pg-props').textContent);
  ok('Eigenschaft still nachgezogen (In Arbeit)', /In Arbeit/.test(pv), pv.slice(0, 200));

  ok('Keine Seitenfehler', !p.errs.length && !s2.p.errs.length, p.errs.concat(s2.p.errs));
  for (const id of [row, db, pg]) await api('DELETE', '/api/notes/' + id).catch(() => null);
  if (was.choice) await D.setEditor(p, was.tiptap ? 'tiptap' : 'editorjs');
  await br.close();
  const n = res.filter(Boolean).length;
  console.log(`\nEIGENE-AENDERUNG ${n}/${res.length}`, n === res.length ? 'ALLES GRUEN' : 'FEHLER');
  process.exit(n === res.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
