// Verweis auf neue Unterseiten am Ende der Elternseite (2.5, Bug 2151, saga/sublink.py) im Browser - beide Editoren
// (Editor.js und Tiptap, Konto-Wahl; Admin editor.engine = choice), Live an/aus (Admin notes.live, features live1).
// Geprueft je Editor: Seitenbaum "+" -> "Leere Seite" -> Elternseite zeigt den Verweis am Ende mit dem aktuellen Titel
// (genau einer, Einleitung unveraendert); Vorlage aus der Galerie unter der Seite -> ein Verweis auf die Startseite;
// Verweis-Auswahl "Neue Seite" (Rechtsklick "Verweis einfuegen …", Tiptap zusaetzlich "[[") -> Verweis nur an der
// Schreibmarke, kein zweiter am Ende; Elternseite offen mit ungespeicherter Eingabe, zweiter Tab legt eine Unterseite an
// -> Eingabe UND Verweis bleiben (Stufe 1: Zusammenfuehren; live: Yjs), kein Neu-laden-Hinweis, keine Seitenfehler.
// Live (Tiptap): Verweis erscheint im offenen Editor ohne Neuladen und ohne PATCH des Inhalts.
// SHOT_DIR=<ordner>: Bilder der Elternseite nach "Neue Unterseite" und nach der Galerie (Dateiname mit SHOT_TAG).
// Aufruf: HK_TEST_BASE=... CK=<Cookie> node tests/browser_unterseiten_link.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const SHOT = process.env.SHOT_DIR || '', TAG = process.env.SHOT_TAG || 'nachher';
const ONLY = process.env.ULINK_EDITORS ? process.env.ULINK_EDITORS.split(',') : ['editorjs', 'tiptap'];
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i).slice(0, 600))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);
const J = (x) => JSON.stringify(x);

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function session() {
    const ctx = await br.newContext({ viewport: { width: 1280, height: 860 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = []; p.patches = [];
    p.on('pageerror', (e) => p.errs.push(e.message));
    p.on('request', (r) => { if (r.method() === 'PATCH' && /\/api\/notes\/\d+$/.test(r.url())) p.patches.push(r.url() + ' ' + (r.postData() || '').slice(0, 80)); });
    await p.goto(BASE + '/notes'); await sleep(600);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return { ctx, p };
  }
  const A = await session(), a = A.p;
  const B = await session(), b = B.p;
  const ver = await a.api('GET', '/api/version');
  const LIVE = (ver.features || []).includes('live1');
  console.log('     notes.live: ' + (LIVE ? 'an' : 'aus'));
  const was = await a.api('GET', '/api/account/editor');
  const made = [];
  const P = (t) => ({ type: 'paragraph', data: { text: t } });
  const mk = async (title, blocks) => {
    const id = (await a.api('POST', '/api/notes', { title, cover: null })).id; made.push(id);
    if (blocks) await a.api('PATCH', '/api/notes/' + id, { body: J({ blocks }), format: 'blocks' });
    return id;
  };
  const get = async (id) => JSON.parse((await a.api('GET', '/api/notes/' + id)).body || '{}').blocks || [];
  const linksIn = (bl, id) => bl.filter((x) => J(x.data || {}).indexOf('/notes#' + id + '\\"') >= 0);
  async function until(fn, ms = 8000) { const end = Date.now() + ms; while (Date.now() < end) { try { if (await fn()) return true; } catch (e) { /* weiter */ } await sleep(120); } return false; }
  async function open(p, id) {
    await p.goto(BASE + '/notes#' + id); await p.reload();
    await until(() => p.evaluate((id) => !!(window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id && !HKPages.k.readOnly()
      && document.querySelector('#pg-editor [data-block-id]')), id), 10000);
    await sleep(500);
  }
  const domLinks = (p, id) => p.evaluate((id) => [].map.call(document.querySelectorAll('#pg-editor a[href="/notes#' + id + '"]'), (x) => x.textContent), id);
  // Elternseite im Baum: "+" klicken (Zeile per Titel)
  async function treePlus(p, title) {
    const row = p.locator('.pg-row', { has: p.locator('.pg-name', { hasText: title }) }).first();
    await row.hover(); await row.locator('.pg-add').click(); await sleep(250);
  }
  async function menu(p, text) { await p.locator('.menu .menu-item', { hasText: text }).first().click(); }

  for (const kind of ONLY) {
    console.log('--- Editor: ' + kind + (LIVE ? ' (live an)' : ' (live aus)') + ' ---');
    if (!(await D.setEditor(a, kind))) { ok(kind + ': Editor umstellbar (Admin editor.engine = choice)', false); continue; }
    await D.setEditor(b, kind);
    // 1) Seitenbaum "+" -> Leere Seite
    const T1 = 'Projekte ' + kind + ' ' + RUN;
    const par = await mk(T1, [P('Einleitung zu den Projekten')]);
    await open(a, par);
    const isTT = await a.evaluate(() => !!document.querySelector('#pg-editor .pm-doc'));
    ok(kind + ': Editor geladen', isTT === (kind === 'tiptap'), isTT);
    await treePlus(a, T1); await menu(a, 'Leere Seite');
    await until(() => a.evaluate((par) => HKPages.k.cur() && HKPages.k.cur().id !== par && HKPages.k.cur().parent_id === par, par));
    const kid = await a.evaluate(() => HKPages.k.cur().id);
    await a.fill('#pg-title', 'Neues Projekt ' + kind); await a.press('#pg-title', 'Tab'); await sleep(1500);
    const bl = await get(par);
    ok(kind + ': Server - Verweis am Ende, Einleitung unveraendert', bl.length === 2 && bl[0].data.text === 'Einleitung zu den Projekten'
       && linksIn(bl, kid).length === 1 && J(bl[1]).indexOf('/notes#' + kid) >= 0, bl);
    await a.locator('.pg-row .pg-name', { hasText: T1 }).first().click();
    await until(async () => (await domLinks(a, kid)).length === 1);
    const dl = await domLinks(a, kid);
    ok(kind + ': Elternseite zeigt den Verweis mit aktuellem Titel', dl.length === 1 && dl[0].indexOf('Neues Projekt ' + kind) >= 0, dl);
    if (SHOT) await a.screenshot({ path: SHOT + '/' + TAG + '-' + kind + '-neue-unterseite.png' });

    // 2) Galerie unter der Seite
    await treePlus(a, T1); await menu(a, 'Aus der Galerie');
    await a.locator('.gal-tile[data-id="wiki"]').click(); await sleep(600);
    const selv = await a.evaluate(() => { const s = document.querySelector('.gal-act select'); return s ? s.options[s.selectedIndex].textContent : null; });
    ok(kind + ': Galerie - "Anlegen unter" ist die Elternseite', selv && selv.indexOf(T1) >= 0, selv);
    await a.locator('.gal-use').click();
    await until(() => a.evaluate((par) => HKPages.k.cur() && HKPages.k.cur().parent_id === par && HKPages.k.cur().id !== par, par), 10000);
    const root = await a.evaluate(() => HKPages.k.cur().id);
    const bl2 = await get(par);
    ok(kind + ': Galerie - genau ein neuer Verweis (Startseite) am Ende', bl2.length === 3 && linksIn(bl2, root).length === 1 && J(bl2[2]).indexOf('/notes#' + root) >= 0, bl2);
    await a.locator('.pg-row .pg-name', { hasText: T1 }).first().click();
    await until(async () => (await domLinks(a, root)).length === 1);
    ok(kind + ': Elternseite zeigt beide Verweise', (await domLinks(a, root)).length === 1 && (await domLinks(a, kid)).length === 1);
    if (SHOT) { await sleep(400); await a.screenshot({ path: SHOT + '/' + TAG + '-' + kind + '-galerie.png' }); }

    // 3) Verweis-Auswahl "Neue Seite" (Rechtsklick) -> nur an der Schreibmarke
    const T3 = 'Auswahl ' + kind + ' ' + RUN;
    const p3 = await mk(T3, [{ id: 'v1', type: 'paragraph', data: { text: 'Hier verlinken' } }]);
    await open(a, p3);
    const bx = await a.locator('#pg-editor [data-block-id="v1"]').boundingBox();
    await a.mouse.click(bx.x + 4, bx.y + bx.height / 2, { button: 'right' }); await sleep(250);
    await a.locator('.menu .menu-item', { hasText: 'Verweis einfügen' }).first().click(); await sleep(250);
    const NEU = 'Frisch ' + kind + ' ' + RUN;
    await a.fill('#pg-picker-q', NEU); await sleep(300); await a.keyboard.press('Enter');
    let nid = null;
    await until(async () => { const r = await a.api('GET', '/api/notes'); const x = (r.notes || r).find((n) => n.title === NEU); nid = x && x.id; return !!nid; });
    await until(async () => linksIn(await get(p3), nid).length >= 1, 8000);
    await sleep(1500);
    const b3 = await get(p3);
    ok(kind + ': "Neue Seite" aus der Auswahl - genau ein Verweis, im Absatz an der Schreibmarke', nid && b3.length === 1
       && linksIn(b3, nid).length === 1 && /verlinken/.test(b3[0].data.text), b3);
    if (kind === 'tiptap') {
      const T4 = 'Klammer ' + RUN;
      const p4 = await mk(T4, [{ id: 'k1', type: 'paragraph', data: { text: 'Start' } }]);
      await open(a, p4);
      await D.focus(a, 'k1', 'end'); await a.keyboard.type(' [[Klammerseite ' + RUN, { delay: 15 }); await sleep(500);
      await a.keyboard.press('Enter');
      let kid4 = null;
      await until(async () => { const r = await a.api('GET', '/api/notes'); const x = (r.notes || r).find((n) => n.title === 'Klammerseite ' + RUN); kid4 = x && x.id; return !!kid4; });
      await sleep(2500);
      const b4 = await get(p4);
      ok('tiptap: "[[" -> neue Seite - genau ein Verweis, im Absatz "Start"', kid4 && linksIn(b4, kid4).length === 1 && b4.length <= 2 && /Start/.test(linksIn(b4, kid4)[0].data.text), b4);
    }

    // 4) Elternseite offen mit ungespeicherter Eingabe, zweiter Tab legt eine Unterseite an
    const T5 = 'Offen ' + kind + ' ' + RUN;
    const p5 = await mk(T5, [{ id: 'o1', type: 'paragraph', data: { text: 'Erster Satz' } }, { id: 'o2', type: 'paragraph', data: { text: 'Zweiter' } }]);
    await open(a, p5); a.patches = [];
    await D.focus(a, 'o1', 'end'); await a.keyboard.type(' und mehr', { delay: 20 });
    const sub = (await b.api('POST', '/api/notes', { parent_id: p5, title: 'Parallel ' + kind })).id;
    await until(async () => (await domLinks(a, sub)).length === 1, 10000);
    ok(kind + ': offener Editor - Verweis kommt an, ohne Neuladen', (await domLinks(a, sub)).length === 1);
    await sleep(2500);
    const b5 = await get(p5);
    ok(kind + ': Eingabe UND Verweis gespeichert (nichts ueberschrieben)', b5.some((x) => x.data && x.data.text === 'Erster Satz und mehr')
       && linksIn(b5, sub).length === 1 && b5.some((x) => x.data && x.data.text === 'Zweiter'), b5);
    const txt = await a.evaluate(() => (document.querySelector('#pg-editor [data-block-id="o1"]') || {}).textContent || '');
    ok(kind + ': Editor zeigt die eigene Eingabe weiter', /Erster Satz und mehr/.test(txt), txt);
    ok(kind + ': kein Konflikt-/Neu-laden-Hinweis', !(await a.evaluate(() => !!document.querySelector('#pg-conflict, #pg-remote'))));
    if (LIVE && kind === 'tiptap') {
      const live = await a.evaluate(() => window.HKPmLive && HKPmLive.state());
      ok('tiptap live: Editor im Live-Modus', live && live.live, live);
      ok('tiptap live: kein PATCH des Inhalts der Elternseite', !a.patches.some((x) => x.indexOf('/api/notes/' + p5 + ' ') >= 0 && /body/.test(x)), a.patches);
    }
    ok(kind + ': keine Seitenfehler', a.errs.length === 0 && b.errs.length === 0, a.errs.concat(b.errs));
  }

  await D.setEditor(a, was && was.tiptap ? 'tiptap' : 'editorjs');
  for (const id of made) await a.api('DELETE', '/api/notes/' + id).catch(() => {});
  await br.close();
  console.log(res.filter(Boolean).length + '/' + res.length, res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER');
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
