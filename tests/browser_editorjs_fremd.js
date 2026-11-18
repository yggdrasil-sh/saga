// Bug 2155 (Saga 2.5): Editor.js - fremde Aenderungen an der offenen Seite, waehrend man tippt. Ein zweiter Tab (gleiches
// Konto, eigene Sitzung) aendert dieselbe Seite mehrfach; erwartet: eigene Eingabe vollstaendig (Server und Editor),
// Fokus/Schreibmarke bleiben (Weitertippen landet im selben Block), fremde Aenderung kommt an, kein "Neu laden"-/
// Konflikt-Hinweis, keine Seitenfehler. Faelle: anderer Block 16x, neue Bloecke, Block geloescht, gleicher Block (ohne
// und mit Ueberschneidung), Unterseiten-Verweis mit Titel in Etappen, Fremdaenderung am Block mit der Schreibmarke ohne
// Eingabe, Spalten (in und neben einer Spalte tippen), viele schnelle Aenderungen, langsames Einarbeiten (Eingabe nur waehrenddessen). Laeuft mit notes.live an und aus (Admin-Schalter; der Test meldet den Stand).
// Voraussetzung: Admin editor.engine = choice (Konto-Wahl Editor.js). FAELLE=a,b,... nur diese Faelle.
// SAGA_EDITOR=tiptap nur zum Vergleich: Tiptap ohne live laesst die Schreibmarke bei fremdem Text vor ihr im selben
// Block noch stehen ("gleich", "ruhend": 3 FAIL, auf 86aab10 genauso); mit live gilt Yjs.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> node tests/browser_editorjs_fremd.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + String(JSON.stringify(i)).slice(0, 600))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const J = (x) => JSON.stringify(x);
const P = (id, t) => ({ id, type: 'paragraph', data: { text: t } });
const KIND = process.env.SAGA_EDITOR === 'tiptap' ? 'tiptap' : 'editorjs';   // Gegenprobe: SAGA_EDITOR=tiptap
const TXT = ' eins zwei drei vier fuenf sechs sieben acht neun zehn', TAIL = ' Schluss';
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function session() {
    const ctx = await br.newContext({ viewport: { width: 1280, height: 860 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = [];
    p.on('pageerror', (e) => p.errs.push(e.message));
    await p.goto(BASE + '/notes'); await D.useEditor(p, KIND);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return p;
  }
  const a = await session(), b = await session();
  const ver = await a.api('GET', '/api/version');
  const LIVE = (ver.features || []).includes('live1');
  console.log('     notes.live: ' + (LIVE ? 'an' : 'aus') + ', Editor: ' + KIND);
  async function until(fn, ms = 8000) { const end = Date.now() + ms; while (Date.now() < end) { try { if (await fn()) return true; } catch (e) { /* */ } await sleep(120); } return false; }
  const getN = async (id) => JSON.parse(J(await b.api('GET', '/api/notes/' + id)));
  const blocksOf = async (id) => JSON.parse((await getN(id)).body || '{}').blocks || [];
  // B: Seite holen, aendern (fn auf die Bloecke), mit merge speichern - wie ein anderer Tab
  async function remote(id, fn) {
    const g = await getN(id); const bl = JSON.parse(g.body).blocks;
    const out = fn(bl) || bl;
    return b.api('PATCH', '/api/notes/' + id, { body: J({ blocks: out }), format: 'blocks', rev: g.rev, merge: 1 });
  }
  // Block mit Fokus und Schreibmarke (Tiptap: ein Eingabefeld fuer alles - daher ueber die Auswahl)
  const activeBlock = () => a.evaluate(() => {
    const e = document.activeElement, s = window.getSelection(), n = s && s.rangeCount ? s.anchorNode : null;
    if (!e || !e.isContentEditable || !n || !e.contains(n)) return null;
    const x = (n.nodeType === 1 ? n : n.parentElement).closest('#pg-editor [data-block-id]');
    return x ? x.getAttribute('data-block-id') : null;
  });
  const domText = (id) => a.evaluate((s) => (document.querySelector(s) || {}).textContent || '', D.block(id));
  async function openA(title, blocks) {
    const id = (await a.api('POST', '/api/notes', { title, cover: null })).id;
    await a.api('PATCH', '/api/notes/' + id, { body: J({ blocks }), format: 'blocks' });
    await a.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
    await until(() => a.evaluate((id) => !!(window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector('#pg-editor [data-block-id="t1"]')), id), 10000);
    await sleep(700);
    return id;
  }
  // Ein Fall: A tippt in t1 (ans Ende), B fuehrt ops aus; danach tippt A ohne neu zu klicken weiter (TAIL)
  async function typingCase(name, blocks, ops, check) {
    console.log('--- ' + name + ' ---');
    const id = await openA('Fremd ' + name, blocks);
    await D.focus(a, 't1', 'end');
    const typing = a.keyboard.type(TXT, { delay: 60 });
    await sleep(250);
    const ctx = {};
    await ops(id, ctx);
    await typing;
    await sleep(400);
    const fb = await activeBlock();
    await a.keyboard.type(TAIL, { delay: 40 });
    await sleep(3500);
    const bl = await blocksOf(id);
    const t1 = bl.find((x) => x.id === 't1');
    if (!ctx.sameBlock) ok(name + ': Eingabe vollstaendig gespeichert', t1 && t1.data.text === 'Start' + TXT + TAIL, bl);
    if (!ctx.sameBlock) ok(name + ': Fokus blieb im Block, Weitertippen landet dort', fb === 't1' && (await domText('t1')) === 'Start' + TXT + TAIL, [fb, await domText('t1')]);
    await check(id, bl, ctx);
    ok(name + ': kein Konflikt-/Neu-laden-Hinweis', !(await a.evaluate(() => !!document.querySelector('#pg-conflict, #pg-remote'))));
    ok(name + ': keine Seitenfehler', a.errs.length === 0 && b.errs.length === 0, a.errs.concat(b.errs));
    a.errs.length = 0; b.errs.length = 0;
    return id;
  }
  const want = (process.env.FAELLE || 'andere16,neu,loeschen,gleich,ueberlappend,unterseite,ruhend,spalten,schnell,langsam').split(',');
  const base3 = () => [P('t1', 'Start'), P('t2', 'Ende'), P('f0', 'fremd 0')];
  const agree = async (id, bl) => {   // Editor (A) zeigt dieselben Bloecke wie der Server
    const ids = (await D.blocks(a)).filter((x) => x.parent == null).map((x) => x.id);
    return J(ids) === J(bl.map((x) => x.id));
  };

  if (want.includes('andere16')) await typingCase('anderer Block 16x', base3(), async (id) => {
    for (let i = 1; i <= 16; i++) { await remote(id, (bl) => { bl.find((x) => x.id === 'f0').data.text = 'fremd ' + i; }); await sleep(120); }
  }, async (id, bl) => {
    ok('anderer Block 16x: fremde Aenderung gespeichert', bl.some((x) => x.id === 'f0' && x.data.text === 'fremd 16'), bl);
    ok('anderer Block 16x: Editor zeigt die fremde Aenderung', await until(async () => (await domText('f0')) === 'fremd 16', 4000), await domText('f0'));
    ok('anderer Block 16x: "Ende" unveraendert', bl.some((x) => x.id === 't2' && x.data.text === 'Ende'), bl);
  });

  if (want.includes('neu')) await typingCase('neue Bloecke', base3(), async (id) => {
    for (let i = 1; i <= 3; i++) { await remote(id, (bl) => { bl.splice(i === 2 ? 1 : bl.length, 0, P('n' + i, 'neu ' + i)); }); await sleep(450); }
  }, async (id, bl) => {
    ok('neue Bloecke: alle drei gespeichert, Reihenfolge', J(bl.map((x) => x.id)) === J(['t1', 'n2', 't2', 'f0', 'n1', 'n3']), bl.map((x) => x.id));
    ok('neue Bloecke: Editor zeigt sie in derselben Reihenfolge', await until(() => agree(id, bl), 4000), (await D.blocks(a)).map((x) => x.id));
  });

  if (want.includes('loeschen')) await typingCase('Block geloescht', base3(), async (id) => {
    await remote(id, (bl) => bl.filter((x) => x.id !== 'f0'));
    await sleep(500);
    await remote(id, (bl) => { bl.push(P('n9', 'danach')); });
  }, async (id, bl) => {
    ok('Block geloescht: bleibt geloescht, neuer Block da', !bl.some((x) => x.id === 'f0') && bl.some((x) => x.id === 'n9'), bl);
    ok('Block geloescht: Editor stimmt mit dem Server ueberein', await until(() => agree(id, bl), 4000), (await D.blocks(a)).map((x) => x.id));
  });

  if (want.includes('gleich')) await typingCase('gleicher Block ohne Ueberschneidung', base3(), async (id, ctx) => {
    ctx.sameBlock = true;
    await sleep(600);
    await remote(id, (bl) => { const t = bl.find((x) => x.id === 't1'); t.data.text = 'Vorne ' + t.data.text; });
    await sleep(600);
    await remote(id, (bl) => { bl.find((x) => x.id === 't2').data.text = 'Ende fremd'; });
  }, async (id, bl) => {
    const t1 = bl.find((x) => x.id === 't1');
    ok('gleicher Block: beide Aenderungen zusammengefuehrt', t1 && t1.data.text === 'Vorne Start' + TXT + TAIL, bl);
    ok('gleicher Block: kein Konfliktblock', !bl.some((x) => x.tunes && x.tunes.hkconflict), bl);
    ok('gleicher Block: Editor zeigt den zusammengefuehrten Text', await until(async () => (await domText('t1')) === 'Vorne Start' + TXT + TAIL, 4000), await domText('t1'));
    ok('gleicher Block: Fokus im Block', (await activeBlock()) === 't1', await activeBlock());
    ok('gleicher Block: andere fremde Aenderung da', bl.some((x) => x.id === 't2' && x.data.text === 'Ende fremd'), bl);
  });

  if (want.includes('ueberlappend')) await typingCase('gleicher Block mit Ueberschneidung', base3(), async (id, ctx) => {
    ctx.sameBlock = true;
    await sleep(900);
    await remote(id, (bl) => { bl.find((x) => x.id === 't1').data.text += ' fremd'; });   // gleiche Stelle wie A: Konflikt
  }, async (id, bl) => {
    const all = bl.map((x) => x.data && x.data.text).join('\n');
    ok('Ueberschneidung: fremde Fassung erhalten', all.indexOf(' fremd') >= 0, bl);
    // wie bei Tiptap: die eingereichte Fassung steht im Konfliktblock, das Weitertippen danach im Original (dessen Text
    // jetzt der fremde ist) - zusammen ist die eigene Eingabe vollstaendig, nichts doppelt
    const k = bl.find((x) => x.tunes && x.tunes.hkconflict), t1 = bl.find((x) => x.id === 't1') || { data: { text: '' } };
    const i = t1.data.text.indexOf(' fremd'), rest = i >= 0 ? t1.data.text.slice(i + 6) : '';
    ok('Ueberschneidung: eigene Eingabe vollstaendig erhalten (Konfliktblock + Weitergetipptes)',
       bl.some((x) => x.data && x.data.text === 'Start' + TXT + TAIL) || (k && k.data.text + rest === 'Start' + TXT + TAIL), bl);
    ok('Ueberschneidung: Konfliktblock (wie bisher)', bl.some((x) => x.tunes && x.tunes.hkconflict), bl);
    const ed = (await D.blocks(a)).filter((x) => x.parent == null).map((x) => x.id);
    ok('Ueberschneidung: Editor zeigt den Stand des Servers', await until(() => agree(id, bl), 4000), [ed, bl.map((x) => x.id)]);
    ok('Ueberschneidung: hoechstens zwei Konfliktbloecke', bl.filter((x) => x.tunes && x.tunes.hkconflict).length <= 2, bl);
  });

  if (want.includes('unterseite')) await typingCase('Unterseiten-Verweis', [P('t1', 'Start'), P('t2', 'Ende')], async (id, ctx) => {
    const word = 'Etappentitel';
    ctx.kid = (await b.api('POST', '/api/notes', { parent_id: id, title: '' })).id;
    for (let i = 1; i <= word.length; i++) { await b.api('PATCH', '/api/notes/' + ctx.kid, { title: word.slice(0, i) }); await sleep(90); }
  }, async (id, bl, ctx) => {
    const lk = bl.filter((x) => J(x.data || {}).indexOf('/notes#' + ctx.kid + '\\"') >= 0);
    ok('Unterseiten-Verweis: genau ein Verweis mit vollem Titel', lk.length === 1 && lk[0].data.text === '<a href="/notes#' + ctx.kid + '">Etappentitel</a>', lk);
    ok('Unterseiten-Verweis: Editor zeigt ihn', await until(async () => (await a.evaluate((k) => !!document.querySelector('#pg-editor a[href="/notes#' + k + '"]'), ctx.kid)), 4000));
    await a.api('DELETE', '/api/notes/' + ctx.kid).catch(() => {});
  });

  if (want.includes('ruhend')) {
    console.log('--- Schreibmarke ohne Eingabe ---');
    const id = await openA('Fremd ruhend', base3());
    await D.focus(a, 't2', 'end');
    await sleep(300);
    await remote(id, (bl) => { bl.find((x) => x.id === 't2').data.text = 'Neu: Ende'; bl.splice(0, 0, P('v1', 'davor')); });
    ok('ruhend: Editor zeigt die fremde Aenderung am Block mit der Schreibmarke', await until(async () => (await domText('t2')) === 'Neu: Ende', 5000), await domText('t2'));
    ok('ruhend: neuer Block davor angekommen', await until(async () => (await domText('v1')) === 'davor', 3000));
    ok('ruhend: Fokus bleibt im Block', (await activeBlock()) === 't2', await activeBlock());
    await a.keyboard.type('!', { delay: 40 });
    await sleep(3000);
    const bl = await blocksOf(id);
    ok('ruhend: Weitertippen landet im Block und wird gespeichert', bl.some((x) => x.id === 't2' && x.data.text === 'Neu: Ende!'), bl);
    ok('ruhend: kein Hinweis, keine Seitenfehler', !(await a.evaluate(() => !!document.querySelector('#pg-conflict, #pg-remote'))) && a.errs.length === 0, a.errs);
    a.errs.length = 0;
  }

  // Spalten (verschachtelte Editoren): fremde Aenderung in einer Spalte und neuer Spaltenblock, waehrend A in t1 tippt;
  // dann umgekehrt: A tippt IN einer Spalte, B aendert t2 und fuegt einen Block an
  const COLS = (k1, k2) => ({ id: 'c1', type: 'columns', data: { cols: [{ blocks: [P('k1', k1)] }, { blocks: [P('k2', k2)] }] } });
  const colText = (bl, kid) => { const c = bl.find((x) => x.id === 'c1'); const all = c ? c.data.cols.flatMap((x) => x.blocks) : []; const k = all.find((x) => x.id === kid); return k ? k.data.text : null; };
  if (want.includes('spalten')) await typingCase('Spalten', [P('t1', 'Start'), P('t2', 'Ende'), COLS('links', 'rechts')], async (id) => {
    await remote(id, (bl) => { bl.find((x) => x.id === 'c1').data.cols[1].blocks[0].data.text = 'rechts fremd'; });
    await sleep(500);
    await remote(id, (bl) => { bl.push({ id: 'c2', type: 'columns', data: { cols: [{ blocks: [P('m1', 'neu links')] }, { blocks: [P('m2', 'neu rechts')] }] } }); });
  }, async (id, bl) => {
    ok('Spalten: fremde Aenderung in der Spalte gespeichert', colText(bl, 'k2') === 'rechts fremd' && colText(bl, 'k1') === 'links', bl);
    ok('Spalten: Editor zeigt sie und den neuen Spaltenblock', await until(async () => (await domText('k2')) === 'rechts fremd' && (await domText('m2')) === 'neu rechts', 4000), [await domText('k2'), await domText('m2')]);
  });
  if (want.includes('spalten')) {
    console.log('--- in einer Spalte tippen ---');
    const id = await openA('Fremd Spalte tippen', [P('t1', 'Start'), P('t2', 'Ende'), COLS('links', 'rechts')]);
    await D.focus(a, 'k1', 'end');
    const typing = a.keyboard.type(TXT, { delay: 60 });
    await sleep(300);
    for (let i = 1; i <= 4; i++) { await remote(id, (bl) => { bl.find((x) => x.id === 't2').data.text = 'Ende ' + i; if (i === 2) bl.splice(0, 0, P('v2', 'oben neu')); }); await sleep(400); }
    await typing; await sleep(400);
    const fb = await activeBlock();
    await a.keyboard.type(TAIL, { delay: 40 });
    await sleep(3500);
    const bl = await blocksOf(id);
    ok('Spalte tippen: Eingabe in der Spalte vollstaendig gespeichert', colText(bl, 'k1') === 'links' + TXT + TAIL, bl);
    ok('Spalte tippen: Fokus blieb in der Spalte', fb === 'k1' && (await domText('k1')) === 'links' + TXT + TAIL, [fb, await domText('k1')]);
    ok('Spalte tippen: fremde Aenderungen da (Server und Editor)', bl.some((x) => x.id === 't2' && x.data.text === 'Ende 4') && bl.some((x) => x.id === 'v2')
       && await until(async () => (await domText('t2')) === 'Ende 4' && (await domText('v2')) === 'oben neu', 4000), bl);
    ok('Spalte tippen: kein Hinweis, keine Seitenfehler', !(await a.evaluate(() => !!document.querySelector('#pg-conflict, #pg-remote'))) && a.errs.length === 0, a.errs);
    a.errs.length = 0;
  }

  if (want.includes('schnell')) await typingCase('viele schnelle Aenderungen', base3(), async (id) => {
    const jobs = [];
    for (let i = 1; i <= 20; i++) {
      jobs.push(remote(id, (bl) => {
        const f = bl.find((x) => x.id === 'f0'); if (f) f.data.text = 'fremd ' + i;
        if (i % 5 === 0) bl.push(P('s' + i, 'schnell ' + i));
      }).catch(() => null));
      await sleep(40);
    }
    await Promise.all(jobs);
  }, async (id, bl) => {
    ok('schnell: alle neuen Bloecke da', ['s5', 's10', 's15', 's20'].every((k) => bl.some((x) => x.id === k)), bl.map((x) => x.id));
    ok('schnell: Editor stimmt mit dem Server ueberein', await until(() => agree(id, bl), 5000), (await D.blocks(a)).map((x) => x.id));
    ok('schnell: "Ende" unveraendert', bl.some((x) => x.id === 't2' && x.data.text === 'Ende'), bl);
  });

  // Pruefung: langsames Einarbeiten (Editor.js blocks.update kuenstlich 2,5 s) - Eingabe NUR waehrenddessen, danach nichts
  // mehr: muss trotzdem gespeichert werden (stand schon im Vergleichsstand S.lastBlocks); Verlassen fragt in der Zeit nach
  if (want.includes('langsam') && KIND === 'editorjs') {
    console.log('--- langsames Einarbeiten ---');
    const id = await openA('Fremd langsam', base3());
    await D.focus(a, 't1', 'end');
    await a.evaluate(() => {
      const e = HKPages.k.editor(), u = e.blocks.update; window.__upd = 0;
      e.blocks.update = function () { const args = arguments; window.__upd++; return new Promise((r) => setTimeout(r, 2500)).then(() => u.apply(null, args)); };
    });
    await remote(id, (bl) => { bl.find((x) => x.id === 'f0').data.text = 'fremd 1'; });
    await until(() => a.evaluate(() => window.__upd > 0), 8000);
    await a.keyboard.type(' abc', { delay: 30 });
    const asked = await a.evaluate(() => { const ev = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(ev); return ev.defaultPrevented; });
    ok('langsam: Verlassen waehrend des Einarbeitens fragt nach', asked === true, asked);
    ok('langsam: Eingabe gespeichert, fremde Aenderung da', await until(async () => { const bl = await blocksOf(id); return bl.some((x) => x.id === 't1' && x.data.text === 'Start abc') && bl.some((x) => x.id === 'f0' && x.data.text === 'fremd 1'); }, 9000), await blocksOf(id));
    ok('langsam: keine Seitenfehler', a.errs.length === 0, a.errs);
    a.errs.length = 0;
  }

  await br.close();
  const n = res.filter(Boolean).length;
  console.log(`EDITORJS-FREMD (${LIVE ? 'live an' : 'live aus'}) ${n}/${res.length}` + (n === res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
