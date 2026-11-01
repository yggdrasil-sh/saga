// Tiptap-Umbau T2 (Kern-Editor, static/pm-editor.js): Abnahmekarten fuer Absatz, Ueberschrift, Zitat, Hinweis, Trennlinie,
// Listen-Matrix, Markdown-Regeln (E5), Slash-Menue (E1), Formatierungsleiste (E8), @/[[/:/Formel, Rollen, Speichern -> GET
// identisch, unveraenderte Fremdbloecke byte-gleich, keine Scheinaenderungen, Undo, Fassade, Tastenkuerzel (keys.js),
// Anwesenheit, Offline, CSP-Verletzungen = 0, Speicher nach Seitenwechseln.
// Braucht den Server mit Admin editor.engine = choice (der Test stellt die Konto-Wahl selbst auf Tiptap und am Ende zurueck).
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> CK2=<Cookie hk-test-b> HANDLE2=<Handle von hk-test-b>
//        node tests/browser_tiptap_kern.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2, HANDLE2 = process.env.HANDLE2;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);
const MOD = process.platform === 'darwin' ? 'Meta' : 'Control';

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function session(ck) {
    const ctx = await br.newContext({ viewport: { width: 1200, height: 900 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: ck, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = [];
    p.on('pageerror', (e) => p.errs.push(e.message));
    await p.addInitScript(() => {
      window.__csp = [];
      document.addEventListener('securitypolicyviolation', (e) => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));
    });
    p.patches = 0;
    p.on('request', (r) => { if (r.method() === 'PATCH' && /\/api\/notes\/\d+$/.test(r.url())) p.patches++; });
    await p.goto(BASE + '/notes'); await sleep(600);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return { ctx, p };
  }
  const A = await session(CK), p = A.p;
  const was = await p.evaluate(() => HK.api('GET', '/api/account/editor'));
  ok('Schalter: Admin erlaubt die Wahl (editor.engine = choice)', was.choice === true, was);
  await D.setEditor(p, 'tiptap');
  const mk = async (title, blocks) => { const id = (await p.api('POST', '/api/notes', { title })).id; if (blocks) await p.api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks }), format: 'blocks' }); return id; };
  const get = async (id) => JSON.parse((await p.api('GET', '/api/notes/' + id)).body || '{}').blocks || [];
  async function open(id, pg = p) {
    await pg.goto(BASE + '/notes#' + id); await pg.reload();
    for (let i = 0; i < 100; i++) { if (await pg.evaluate((id) => window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector('#pg-editor .pm-doc') && !document.body.classList.contains('x'), id)) break; await sleep(50); }
    await sleep(700);
    pg.patches = 0;
  }
  async function saved(id, ms = 1500) { await sleep(ms); return get(id); }
  const caretEnd = async (id) => { await D.focus(p, id, 'end'); };
  const html = (b) => b && b.data ? b.data.text : null;

  // ---------------------------------------------------------------------------------------------------------------
  // 1) Laden, Karten, Durchreichung
  const FOREIGN = [
    { id: 'c1', type: 'code', data: { code: 'print(1)\nx', language: 'python' } },
    { id: 't1', type: 'toggle', data: { text: 'Altformat bleibt', open: false } },
    { id: 'cl1', type: 'columns', data: { cols: [{ blocks: [{ id: 'cc1', type: 'paragraph', data: { text: 'L' } }] }, { w: 0.4, blocks: [] }] } },
    { id: 'tb1', type: 'table', data: { withHeadings: true, content: [['<b>a</b>', 'b'], ['c', 'd']] } },
    { id: 'u1', type: 'checklist', data: { items: [{ text: 'alt', checked: true }] } },
    { id: 'w1', type: 'widget', data: { kind: 'clock', zz: 1 } },
    { id: 'm1', type: 'math', data: { tex: 'a^2' } }
  ];
  const KERN = [
    { id: 'p1', type: 'paragraph', data: { text: 'Hallo <strong>Welt</strong> und <span class="hk-c-red">rot</span>' } },
    { id: 'h1', type: 'header', data: { text: 'Titel', level: 3 }, tunes: { hkstyle: { c: 'blue' } } },
    { id: 'q1', type: 'quote', data: { text: 'Zitat', caption: 'Quelle', alignment: 'center' } },
    { id: 'k1', type: 'callout', data: { emoji: '💡', text: 'Hinweis' } },
    { id: 'd1', type: 'delimiter', data: {} },
    { id: 'l1', type: 'list', data: { style: 'ordered', meta: { start: 3, counterType: 'lower-roman' }, items: [{ content: 'eins', meta: {}, items: [] }] } }
  ];
  const P1 = await mk('TT Kern ' + RUN, KERN.concat(FOREIGN));
  const orig = await get(P1);
  await open(P1);
  ok('Editor ist Tiptap (Fassade kind tiptap)', await D.isEditor(p) === 'tiptap', await D.isEditor(p));
  const tops = await p.evaluate(() => HKPages.k.doc.blocks().filter((b) => b.parent == null).map((b) => b.id + ':' + b.type));
  ok('Alle Bloecke tragen data-block-id/-type (Fassade blocks)', tops.join(',') === orig.map((b) => b.id + ':' + b.type).join(','), tops);
  const cards = await p.evaluate(() => [...document.querySelectorAll('#pg-editor .pm-card')].map((c) => c.getAttribute('data-block-type')));
  // T3: Toggle und Spalten sind echte Knoten (tests/browser_tiptap_struktur.js), keine Karten mehr
  ok('Nicht unterstuetzte Typen als Karte (code, table, checklist, math), Toggle/Spalten nicht', ['code', 'table', 'checklist', 'math'].every((t) => cards.includes(t)) && !cards.includes('toggle') && !cards.includes('columns'), cards);
  ok('Karte nennt "im klassischen Editor bearbeiten"', /klassischen Editor/.test(await p.textContent('#pg-editor .pm-card')));
  const widgetView = await p.evaluate(() => !!document.querySelector('#pg-editor .hk-tool-widget'));
  ok('Widget mit Werkzeug-Ansicht (pm-tools-daten) statt Karte', widgetView);
  const same0 = await p.evaluate(() => HKPages.k.editor().save().then((o) => JSON.stringify(o.blocks)));
  ok('Ohne Bearbeitung: save() byte-gleich mit GET', same0 === JSON.stringify(orig), [same0.slice(0, 200), JSON.stringify(orig).slice(0, 200)]);
  const st = await p.evaluate(() => ({ cls: document.querySelector('[data-block-id="h1"]').className, al: document.querySelector('[data-block-id="q1"]').className,
    cr: document.querySelector('[data-block-id="l1"] ol').style.counterReset, ct: document.querySelector('[data-block-id="l1"]').className }));
  ok('Blockfarbe, Zitat-Ausrichtung, Listen-Start/Zaehlart dargestellt', /hk-c-blue/.test(st.cls) && /pm-al-center/.test(st.al) && /pmli 2/.test(st.cr) && /pm-ct-lower-roman/.test(st.ct), st);
  await sleep(1500);
  ok('Keine Scheinaenderung: Laden schickt kein PATCH', p.patches === 0, p.patches);
  ok('Stiller Speicherstatus (E9): kein "Ungespeichert"', !/Ungespeichert/.test(await p.textContent('#pg-state')));

  // 2) Tippen -> speichern -> GET; Fremdbloecke byte-gleich
  await caretEnd('p1'); await p.keyboard.type(' plus', { delay: 15 });
  ok('Beim Tippen kein "Ungespeichert …" (E9)', !/Ungespeichert|Speichert/.test(await p.textContent('#pg-state')));
  let b = await saved(P1);
  ok('Tippen gespeichert, Normalform (strong -> b, span aussen; Farbe laeuft am Ende weiter)', html(b[0]) === 'Hallo <b>Welt</b> und <span class="hk-c-red">rot plus</span>', b[0]);
  const fx = (l) => l.filter((x) => FOREIGN.some((f) => f.id === x.id)).map((x) => JSON.stringify(x));
  ok('Unveraenderte Fremdbloecke byte-gleich (code, toggle-Altformat, columns, table, unbekannt, widget, math)', JSON.stringify(fx(b)) === JSON.stringify(fx(orig)), [fx(b), fx(orig)]);
  ok('Unveraenderte Kern-Bloecke byte-gleich', JSON.stringify(b.slice(1, 6)) === JSON.stringify(orig.slice(1, 6)), b.slice(1, 6));
  const viaEd = await p.evaluate(() => HKPages.k.editor().save().then((o) => JSON.stringify(o.blocks)));
  ok('Speichern -> GET identisch mit Editorstand', viaEd === JSON.stringify(b), [viaEd.slice(0, 150)]);
  // Karten sind geschuetzt: Tippen auf eine Karte aendert nichts
  await p.click('[data-block-id="c1"]'); await p.keyboard.type('zz'); await sleep(300);
  const c1 = await p.evaluate(() => HKPages.k.doc.byId('c1').json());
  ok('Karte: Tippen aendert den Inhalt nicht', c1 && c1.data.code === 'print(1)\nx', c1);
  // Callout-Symbol und Zitat-Ausrichtung
  await p.click('[data-block-id="k1"] .pm-emo');
  await p.click('[data-block-id="q1"] .pm-qal', { force: true });
  b = await saved(P1);
  const k1 = b.find((x) => x.id === 'k1'), q1 = b.find((x) => x.id === 'q1');
  ok('Hinweis: Symbol wechselt (Rotation)', k1 && k1.data.emoji === '⚠️', k1);
  ok('Zitat: Ausrichtung umgeschaltet, Text/Quelle bleiben', q1 && q1.data.alignment === 'left' && q1.data.text === 'Zitat' && q1.data.caption === 'Quelle', q1);
  ok('Zitat/Hinweis: Fremdbloecke weiter byte-gleich', JSON.stringify(fx(b)) === JSON.stringify(fx(orig)));

  // ---------------------------------------------------------------------------------------------------------------
  // 3) Markdown-Regeln (E5) + Marken
  const P2 = await mk('TT Markdown', [{ id: 's0', type: 'paragraph', data: { text: 'Start' } }]);
  await open(P2);
  async function line(s) { await p.keyboard.press('Enter'); await p.keyboard.type(s, { delay: 12 }); }
  await caretEnd('s0');
  await line('# Eins'); await line('## Zwei'); await line('### Drei'); await line('#### Vier');
  await p.keyboard.press('Enter'); await line('- punkt'); await p.keyboard.press('Enter'); await p.keyboard.press('Enter');
  await p.keyboard.type('1. nummer', { delay: 12 }); await p.keyboard.press('Enter'); await p.keyboard.press('Enter');
  await p.keyboard.type('[] haken', { delay: 12 }); await p.keyboard.press('Enter'); await p.keyboard.press('Enter');
  await p.keyboard.type('> zitat', { delay: 12 }); await p.keyboard.press('Enter'); await p.keyboard.press('Enter');
  await p.keyboard.type('" zitat2', { delay: 12 }); await p.keyboard.press('Enter'); await p.keyboard.press('Enter');
  await p.keyboard.type('---', { delay: 12 });
  await p.keyboard.type('a **fett** b *kursiv* c `code` d ~~weg~~ e ==gelb== f $x^2$ g', { delay: 8 });
  b = await saved(P2, 1800);
  const kinds = b.map((x) => x.type + (x.data.level ? x.data.level : x.data.style ? ':' + x.data.style : ''));
  ok('# … #### -> Ueberschrift 1-4', ['header1', 'header2', 'header3', 'header4'].every((k) => kinds.includes(k)), kinds);
  ok('- / 1. / [] -> Listen (3 Stile)', ['list:unordered', 'list:ordered', 'list:checklist'].every((k) => kinds.includes(k)), kinds);
  const lch = b.find((x) => x.type === 'list' && x.data.style === 'checklist');
  ok('[] -> Haeckchen-Punkt mit checked:false', lch && lch.data.items[0].meta.checked === false && lch.data.items[0].content === 'haken', lch);
  ok('> -> Toggle, " -> Zitat (Notion-Belegung, E5)', b.filter((x) => x.type === 'quote').length === 1 && b.filter((x) => x.type === 'toggle').length === 1, kinds);
  ok('--- -> Trennlinie + Absatz danach', kinds.includes('delimiter') && b[b.length - 1].type === 'paragraph', kinds);
  const last = html(b[b.length - 1]);
  ok('Marken-Kuerzel: **b** *i* `code` ~~s~~ ==mark== $tex$', last === 'a <b>fett</b> b <i>kursiv</i> c <code class="inline-code">code</code> d <s>weg</s> e <mark class="cdx-marker">gelb</mark> f <code class="hk-math">x^2</code> g', last);
  // Undo (Gruppierung): letzte Eingabe zurueck
  await p.keyboard.type(' undo', { delay: 10 }); await sleep(700);
  await p.keyboard.press(MOD + '+z'); await sleep(200);
  const un = await p.evaluate(() => HKPages.k.doc.blocks().slice(-1)[0].el.textContent);
  ok('Undo nimmt die letzte Eingabegruppe zurueck', !/undo/.test(un) && /g$/.test(un.trim()), un);
  await p.keyboard.press(MOD + '+Shift+z'); await sleep(200);
  ok('Redo stellt sie wieder her', /undo$/.test((await p.evaluate(() => HKPages.k.doc.blocks().slice(-1)[0].el.textContent)).trim()));

  // ---------------------------------------------------------------------------------------------------------------
  // 4) Listen-Matrix
  const L = (style, items, meta) => ({ id: 'L', type: 'list', data: { style, meta: meta || {}, items } });
  const I = (c, items, meta) => ({ content: c, meta: meta || {}, items: items || [] });
  const shape = (items) => (items || []).map((i) => i.content + (i.items && i.items.length ? '[' + shape(i.items) + ']' : '')).join(',');
  async function listCase(name, style, items, act, check) {
    const id = await mk('TT Liste ' + name, [{ id: 'pa', type: 'paragraph', data: { text: 'vor' } }, L(style, items), { id: 'pz', type: 'paragraph', data: { text: 'nach' } }]);
    await open(id);
    await act();
    const bl = await saved(id, 1300);
    let r; try { r = check(bl); } catch (e) { r = false; }
    ok('Liste [' + style + '] ' + name, r, bl.map((x) => x.type + ':' + (x.data.items ? shape(x.data.items) : x.data.text)));
    return bl;
  }
  async function inItem(text, where = 'end') {
    await p.evaluate(([t, w]) => {
      const ed = HKPages.k.editor().editor(), S0 = SagaPM.pm.state; let pos = null;
      ed.state.doc.descendants((n, p) => { if (pos == null && n.type.name === 'ltext' && n.textContent === t) pos = p + 1 + (w === 'end' ? n.content.size : w === 'start' ? 0 : w); });
      ed.view.dispatch(ed.state.tr.setSelection(S0.TextSelection.create(ed.state.doc, pos))); ed.view.focus();
    }, [text, where]);
  }
  const lst = (bl) => bl.filter((x) => x.type === 'list');
  for (const style of ['unordered', 'ordered', 'checklist']) {
    const m = style === 'checklist' ? { checked: false } : {};
    await listCase('Enter am Ende -> neuer Punkt', style, [I('a', [], m)], async () => { await inItem('a'); await p.keyboard.press('Enter'); await p.keyboard.type('b'); },
      (bl) => shape(lst(bl)[0].data.items) === 'a,b' && (style !== 'checklist' || lst(bl)[0].data.items[1].meta.checked === false));
    await listCase('Enter in der Mitte teilt den Punkt', style, [I('abcd', [], m)], async () => { await inItem('abcd', 2); await p.keyboard.press('Enter'); },
      (bl) => shape(lst(bl)[0].data.items) === 'ab,cd');
    await listCase('Enter auf leerem letztem Punkt -> Absatz nach der Liste', style, [I('a', [], m)], async () => { await inItem('a'); await p.keyboard.press('Enter'); await p.keyboard.press('Enter'); await p.keyboard.type('frei'); },
      (bl) => bl.map((x) => x.type).join() === 'paragraph,list,paragraph,paragraph' && bl[2].data.text === 'frei' && shape(bl[1].data.items) === 'a');
    await listCase('Enter auf leerem mittlerem Punkt teilt die Liste (Rest mit neuer ID)', style, [I('a', [], m), I('x', [], m), I('c', [], m)], async () => {
      await inItem('x'); for (let i = 0; i < 1; i++) await p.keyboard.press('Backspace'); await p.keyboard.press('Enter');
    }, (bl) => { const l = lst(bl); return l.length === 2 && shape(l[0].data.items) === 'a' && shape(l[1].data.items) === 'c' && l[0].id === 'L' && l[1].id !== 'L' && bl[2].type === 'paragraph'; });
    await listCase('Tab rueckt ein', style, [I('a', [], m), I('b', [], m)], async () => { await inItem('b'); await p.keyboard.press('Tab'); },
      (bl) => shape(lst(bl)[0].data.items) === 'a[b]');
    await listCase('Tab auf erstem Punkt: nichts', style, [I('a', [], m), I('b', [], m)], async () => { await inItem('a'); await p.keyboard.press('Tab'); await p.keyboard.type('!'); },
      (bl) => shape(lst(bl)[0].data.items) === 'a!,b');
    await listCase('Umschalt+Tab rueckt aus', style, [I('a', [I('b', [], m)], m)], async () => { await inItem('b'); await p.keyboard.press('Shift+Tab'); },
      (bl) => shape(lst(bl)[0].data.items) === 'a,b');
    await listCase('Umschalt+Tab: folgende Geschwister werden Unterpunkte', style, [I('a', [I('b', [], m), I('c', [], m)], m)], async () => { await inItem('b'); await p.keyboard.press('Shift+Tab'); },
      (bl) => shape(lst(bl)[0].data.items) === 'a,b[c]');
    await listCase('Umschalt+Tab oberste Ebene: nichts', style, [I('a', [], m)], async () => { await inItem('a'); await p.keyboard.press('Shift+Tab'); },
      (bl) => shape(lst(bl)[0].data.items) === 'a');
    await listCase('Enter auf leerem Unterpunkt rueckt aus', style, [I('a', [I('b', [], m)], m)], async () => { await inItem('b'); await p.keyboard.press('Enter'); await p.keyboard.press('Enter'); await p.keyboard.type('n'); },
      (bl) => shape(lst(bl)[0].data.items) === 'a[b],n');
    await listCase('Enter am Ende eines Punktes mit Unterpunkten -> erster Unterpunkt', style, [I('a', [I('b', [], m)], m)], async () => { await inItem('a'); await p.keyboard.press('Enter'); await p.keyboard.type('n'); },
      (bl) => shape(lst(bl)[0].data.items) === 'a[n,b]');
    await listCase('Rueckschritt am Anfang eines Unterpunkts rueckt aus', style, [I('a', [I('b', [], m)], m)], async () => { await inItem('b', 'start'); await p.keyboard.press('Backspace'); },
      (bl) => shape(lst(bl)[0].data.items) === 'a,b');
    await listCase('Rueckschritt am Anfang des ersten Punkts -> Absatz vor der Liste', style, [I('a', [], m), I('b', [], m)], async () => { await inItem('a', 'start'); await p.keyboard.press('Backspace'); },
      (bl) => bl.map((x) => x.type).join() === 'paragraph,paragraph,list,paragraph' && bl[1].data.text === 'a' && shape(bl[2].data.items) === 'b');
    await listCase('Rueckschritt am Anfang eines weiteren Punkts haengt an den vorigen an', style, [I('a', [], m), I('b', [I('c', [], m)], m)], async () => { await inItem('b', 'start'); await p.keyboard.press('Backspace'); },
      (bl) => shape(lst(bl)[0].data.items) === 'ab[c]');
  }
  // Haekchen, Stilwechsel mit Verschachtelung, Start/Zaehlart bleiben
  await listCase('Haekchen anklicken', 'checklist', [I('a', [], { checked: false })], async () => { await p.click('#pg-editor .pm-check'); },
    (bl) => lst(bl)[0].data.items[0].meta.checked === true);
  await listCase('Stilwechsel per Slash behaelt Verschachtelung', 'unordered', [I('a', [I('b')])], async () => {
    await inItem('b', 'start'); await p.keyboard.type('/Nummer'); await sleep(300); await p.keyboard.press('Enter');
  }, (bl) => lst(bl)[0].data.style === 'ordered' && shape(lst(bl)[0].data.items) === 'a[b]');
  await listCase('Stilwechsel zu Checkliste setzt checked:false, meta.start/counterType bleiben', 'ordered', [I('a', [I('b')])], async () => {
    await inItem('a', 'start'); await p.keyboard.type('/Checkl'); await sleep(300); await p.keyboard.press('Enter');
  }, (bl) => lst(bl)[0].data.style === 'checklist' && lst(bl)[0].data.items[0].meta.checked === false && lst(bl)[0].data.items[0].items[0].meta.checked === false);
  const LM = await mk('TT Liste meta', [L('ordered', [I('x')], { start: 5, counterType: 'upper-alpha' })]);
  await open(LM); await inItem('x'); await p.keyboard.type('y'); const lmb = await saved(LM);
  ok('Liste: meta.start/counterType bleiben beim Bearbeiten', JSON.stringify(lmb[0].data.meta) === JSON.stringify({ start: 5, counterType: 'upper-alpha' }) && lmb[0].data.items[0].content === 'xy', lmb[0]);

  // ---------------------------------------------------------------------------------------------------------------
  // 5) Slash-Menue (E1)
  const P3 = await mk('TT Slash ' + RUN, [{ id: 'a0', type: 'paragraph', data: { text: 'eins' } }, { id: 'a1', type: 'paragraph', data: { text: '' } }]);
  await open(P3);
  await D.focus(p, 'a1', 'end'); await p.keyboard.type('/');
  await p.waitForSelector(D.UI.popover, { timeout: 3000 }).catch(() => {});
  const groups = await p.evaluate(() => [...document.querySelectorAll('.pm-pop:not([hidden]) .hk-at-g')].map((g) => g.textContent));
  ok('Slash: Menue unter dem Cursor mit Gruppen (Grundblöcke, Saga, Datenbank)', groups.includes('Grundblöcke') && groups.includes('Saga') && groups.includes('Datenbank'), groups);
  const it0 = await p.evaluate(() => { const b = document.querySelector('.pm-pop:not([hidden]) .hk-emo-i'); return b && { t: b.querySelector('.hk-at-t').textContent, d: !!b.querySelector('.pm-mi-d'), k: !!b.querySelector('.pm-mi-k') }; });
  ok('Slash: Eintrag mit Symbol, Name, Beschreibung, Kuerzel', it0 && it0.t === 'Text' && it0.d && it0.k, it0);
  ok('Slash: kein Suchfeld', !(await p.$('.pm-pop:not([hidden]) input')));
  await p.keyboard.type('über'); await sleep(200);
  const flt = await p.evaluate(() => [...document.querySelectorAll('.pm-pop:not([hidden]) .hk-at-t')].map((x) => x.textContent));
  ok('Slash: Tippen filtert ("über" -> Überschriften zuerst)', flt[0] === 'Überschrift 1' && flt.length <= 8, flt);
  await p.keyboard.press('ArrowDown'); await p.keyboard.press('Enter'); await sleep(200);
  b = await p.evaluate(() => HKPages.k.doc.byId('a1').json());
  ok('Slash im leeren Absatz wandelt um (ID bleibt): Überschrift 2', b && b.type === 'header' && b.data.level === 2 && b.data.text === '', b);
  await p.keyboard.type('Kopf'); await p.keyboard.press('Enter'); await p.keyboard.type('/xyz'); await sleep(200);
  await p.keyboard.press('Escape'); await sleep(150);
  const escTxt = await p.evaluate(() => HKPages.k.doc.current().el.textContent);
  ok('Slash: Esc laesst "/…" stehen', escTxt === '/xyz' && !(await p.$(D.UI.popover)), escTxt);
  await p.keyboard.press('Control+a').catch(() => {});
  await D.focus(p, 'a0', 'end'); await p.keyboard.type(' /rot hintergrund'); await sleep(250); await p.keyboard.press('Enter'); await sleep(200);
  b = await p.evaluate(() => HKPages.k.doc.byId('a0').json());
  ok('Farb-Slash "/rot hintergrund" faerbt den Block', b && JSON.stringify(b.tunes.hkstyle) === JSON.stringify({ c: 'red', bg: true }) && b.data.text === 'eins ', b);
  await D.focus(p, 'a0', 'end'); await p.keyboard.type('/Trenn'); await sleep(200); await p.keyboard.press('Enter'); await sleep(200);
  const after = await p.evaluate(() => HKPages.k.doc.blocks().map((x) => x.type));
  ok('Slash in Absatz mit Text: neuer Block darunter (Trennlinie + Absatz)', after[0] === 'paragraph' && after[1] === 'delimiter' && after[2] === 'paragraph', after);
  await p.keyboard.type('/Aufgaben'); await sleep(250); await p.keyboard.press('Enter'); await sleep(500);
  ok('Slash: Werkzeug aus HKTools (Aufgaben) einfuegen', await p.evaluate(() => HKPages.k.doc.blocks().some((x) => x.type === 'tasks')));

  // ---------------------------------------------------------------------------------------------------------------
  // 6) Formatierungsleiste (E8)
  const P4 = await mk('TT Leiste', [{ id: 'b1', type: 'paragraph', data: { text: 'eins zwei drei' } }]);
  await open(P4);
  async function selWord(w) {
    await p.evaluate((w) => {
      const ed = HKPages.k.editor().editor(), S0 = SagaPM.pm.state; let a = null;
      ed.state.doc.descendants((n, pos) => { if (a == null && n.isText && n.text.indexOf(w) >= 0) a = pos + n.text.indexOf(w); });
      ed.view.focus(); ed.view.dispatch(ed.state.tr.setSelection(S0.TextSelection.create(ed.state.doc, a, a + w.length)));
    }, w);
    await sleep(350);
  }
  await selWord('zwei');
  const bub = await p.evaluate(() => { const b = document.querySelector('.pm-bubble'); return b && { vis: getComputedStyle(b).visibility !== 'hidden' && b.offsetWidth > 0, a: [...b.querySelectorAll('.pm-bb')].map((x) => x.dataset.a) }; });
  ok('Leiste erscheint bei Textauswahl', bub && bub.vis, bub);
  ok('Leiste: Reihenfolge Umwandeln · B I U S · Code · Link · Farbe · Kommentar (· KI)', bub && bub.a.join(',').indexOf('conv,b,i,u,s,icode,math,link,color,comment') === 0, bub && bub.a);
  await p.click('.pm-bubble [data-a="b"]'); await p.click('.pm-bubble [data-a="i"]');
  await p.click('.pm-bubble [data-a="color"]'); await p.click('.menu .menu-item:has-text("Rot")').catch(() => {});
  await sleep(200);
  await selWord('drei'); await p.click('.pm-bubble [data-a="link"]'); await p.fill('.pm-bubble .pm-blink input', 'example.org/x'); await p.keyboard.press('Enter');
  await sleep(200);
  await selWord('eins'); await p.click('.pm-bubble [data-a="u"]'); await p.click('.pm-bubble [data-a="icode"]');
  b = await saved(P4);
  ok('Leiste: B/I/Farbe/Link/U/Code ergeben Normalform (Rangfolge link > color > b > i > u > icode)',
    html(b[0]) === '<u><code class="inline-code">eins</code></u> <span class="hk-c-red"><b><i>zwei</i></b></span> <a href="https://example.org/x" target="_blank" rel="noopener noreferrer">drei</a>', html(b[0]));
  await selWord('zwei'); await p.click('.pm-bubble [data-a="conv"]'); await p.click('.menu .menu-item:has-text("Überschrift 2")').catch(() => {});
  b = await saved(P4);
  ok('Leiste: Umwandeln in Überschrift 2 (ID bleibt, Marken bleiben)', b[0].type === 'header' && b[0].id === 'b1' && b[0].data.level === 2 && /<b><i>zwei/.test(b[0].data.text), b[0]);

  // ---------------------------------------------------------------------------------------------------------------
  // 7) @ Erwaehnung/Datum, [[ Verweis, : Emoji, Formel, Kontextmenue
  const P5 = await mk('TT Inline', [{ id: 'i1', type: 'paragraph', data: { text: 'x' } }]);
  if (HANDLE2) await p.api('PUT', '/api/notes/' + P5 + '/shares', { type: 'user', target: HANDLE2, role: 'edit' });
  await open(P5);
  await D.focus(p, 'i1', 'end'); await p.keyboard.type(' @Bert', { delay: 30 });
  await sleep(900);
  const atItems = await p.evaluate(() => [...document.querySelectorAll('.pm-pop:not([hidden]) .hk-at-t')].map((x) => x.textContent));
  ok('@: Personen vom Server', atItems.some((t) => /Bert/.test(t)), atItems);
  await p.keyboard.press('Enter'); await sleep(200);
  await p.keyboard.type('@morgen', { delay: 30 }); await sleep(500); await p.keyboard.press('Enter'); await sleep(200);
  await p.keyboard.type('[[TT Kern ' + RUN, { delay: 25 }); await sleep(400); await p.keyboard.press('Enter'); await sleep(200);
  await p.keyboard.type(':herz', { delay: 30 }); await sleep(300); await p.keyboard.press('Enter'); await sleep(200);
  b = await saved(P5);
  const t5 = html(b[0]);
  ok('@Person -> span.hk-mention data-u', HANDLE2 ? new RegExp('<span class="hk-mention" data-u="' + HANDLE2.replace(/[~]/g, '~') + '">Bert Test</span>').test(t5) : /hk-mention/.test(t5), t5);
  ok('@morgen -> time.hk-date', /<time datetime="\d{4}-\d{2}-\d{2}" class="hk-date">[^<]+<\/time>/.test(t5), t5);
  ok('[[ -> Seitenverweis <a href="/notes#id">', new RegExp('<a href="/notes#' + P1 + '">TT Kern ' + RUN + '</a>').test(t5), t5);
  ok(': -> Emoji', /❤️/.test(t5), t5);
  // Datum bearbeiten ueber den Chip
  await p.click('#pg-editor time.hk-date'); await sleep(200);
  ok('Klick auf Datum oeffnet Bearbeiten', !!(await p.$('.hk-date-edit input[type=date]')));
  await p.keyboard.press('Escape');
  // Formel $..$ und Popover
  await D.focus(p, 'i1', 'end'); await p.keyboard.type(' $a+b$ ', { delay: 20 }); await sleep(150);
  await p.click('#pg-editor code.hk-math'); await sleep(200);
  ok('Klick auf Formel oeffnet das Formel-Feld', !!(await p.$('.hk-mpop input')));
  await p.fill('.hk-mpop input', 'c^2'); await p.keyboard.press('Enter');
  b = await saved(P5);
  ok('Formel bearbeitet -> <code class="hk-math">c^2</code>', /<code class="hk-math">c\^2<\/code>/.test(html(b[0])), html(b[0]));
  // Kontextmenue "Verweis einfuegen …"
  const box = await p.locator('[data-block-id="i1"]').boundingBox();
  await p.mouse.click(box.x + 4, box.y + box.height / 2, { button: 'right' }); await sleep(200);
  const ctxm = await p.$('.menu .menu-item:has-text("Verweis einfügen")');
  ok('Rechtsklick: "Verweis einfügen …"', !!ctxm);
  if (ctxm) { await ctxm.click(); await sleep(200); await p.fill('#pg-picker-q', 'TT Slash ' + RUN); await sleep(200); await p.keyboard.press('Enter'); }
  b = await saved(P5);
  ok('Kontextmenue-Verweis eingefuegt', new RegExp('<a href="/notes#' + P3 + '">TT Slash ' + RUN + '</a>').test(html(b[0])), html(b[0]));

  // ---------------------------------------------------------------------------------------------------------------
  // 8) Fassade, keys.js-Kuerzel, Anwesenheit
  const P6 = await mk('TT Fassade', [{ id: 'f1', type: 'paragraph', data: { text: 'eins' } }, { id: 'f2', type: 'paragraph', data: { text: 'zwei' } }]);
  await open(P6);
  const fa = await p.evaluate(async () => {
    const d = HKPages.k.doc, out = {};
    out.ins = await d.insertAfter('f1', [{ type: 'header', data: { text: 'neu', level: 2 }, tunes: { hkstyle: { c: 'green' } } }], { focus: 'end' });
    out.cur = d.current() && d.current().id;
    out.rep = await d.replace([out.ins[0]], [{ id: out.ins[0], type: 'header', data: { text: 'neu2', level: 3 } }]);
    out.mv = d.move('f2', -1);
    out.order = d.blocks().map((b) => b.id);
    d.select(['f1', 'f2']); out.sel = d.selected().map((b) => b.id);
    out.selCls = !!document.querySelector('[data-block-id="f1"].pm-sel');
    d.select([]);
    out.rm = await d.remove(['f1']);
    out.focus = d.focus('f2', 'start');
    out.json = await d.byId('f2').json();
    return out;
  });
  ok('Fassade: insertAfter/current/replace/move/select/remove/focus/json', fa.ins.length === 1 && fa.cur === fa.ins[0] && fa.rep[0] === fa.ins[0] &&
    fa.mv && fa.order.join() === ['f1', 'f2', fa.ins[0]].join() && fa.sel.join() === 'f1,f2' && fa.selCls && fa.rm[0] === 'f1' && fa.focus && fa.json.data.text === 'zwei', fa);
  await D.focus(p, 'f2', 'end'); await p.keyboard.press(MOD + '+Alt+1'); await sleep(200);
  ok('keys.js: Strg+Alt+1 -> Überschrift 1 (über die Fassade)', (await p.evaluate(() => HKPages.k.doc.blocks()[0].type)) === 'header');
  await p.keyboard.press(MOD + '+d'); await sleep(200);
  ok('keys.js: Strg+D dupliziert', (await D.count(p, { top: true })) === 3);
  ok('Anwesenheit Stufe 1: Block-ID der Schreibmarke (focusBlock)', await p.evaluate(() => !!HKPages.k.focusBlock()));

  // ---------------------------------------------------------------------------------------------------------------
  // 9) Rollen: Lesen / gesperrt
  const P7 = await mk('TT Rollen', [{ id: 'r1', type: 'paragraph', data: { text: 'nur lesen' } }]);
  if (CK2 && HANDLE2) {
    await p.api('PUT', '/api/notes/' + P7 + '/shares', { type: 'user', target: HANDLE2, role: 'view' });
    const B = await session(CK2);
    await D.setEditor(B.p, 'tiptap');
    await open(P7, B.p);
    const roB = await B.p.evaluate(() => ({ ce: document.querySelector('#pg-editor .pm-doc').getAttribute('contenteditable'), kind: HKPages.k.doc.kind }));
    ok('Rolle Lesen: Editor nicht bearbeitbar (editable:false)', roB.ce === 'false' && roB.kind === 'tiptap', roB);
    await B.p.click('[data-block-id="r1"]'); await B.p.keyboard.type('xx'); await sleep(1300);
    ok('Rolle Lesen: Tippen aendert nichts, kein PATCH', B.p.patches === 0 && (await get(P7))[0].data.text === 'nur lesen');
    ok('Rolle Lesen: keine Seitenfehler', !B.p.errs.length, B.p.errs);
    await B.ctx.close();
  }
  await p.api('PATCH', '/api/notes/' + P7, { locked: true });
  await open(P7);
  ok('Gesperrte Seite: editable:false', (await p.evaluate(() => document.querySelector('#pg-editor .pm-doc').getAttribute('contenteditable'))) === 'false');
  await p.api('PATCH', '/api/notes/' + P7, { locked: false });

  // ---------------------------------------------------------------------------------------------------------------
  // 10) Zusammenfuehren: fremde Aenderung waehrend des Tippens (PATCH merge:1, mergedReply blockweise)
  const P8 = await mk('TT Merge', [{ id: 'm1', type: 'paragraph', data: { text: 'meins' } }, { id: 'm2', type: 'paragraph', data: { text: 'fremd' } }]);
  await open(P8);
  const n8 = await p.api('GET', '/api/notes/' + P8);
  await p.api('PATCH', '/api/notes/' + P8, { body: JSON.stringify({ blocks: [{ id: 'm1', type: 'paragraph', data: { text: 'meins' } }, { id: 'm2', type: 'paragraph', data: { text: 'fremd geändert' } }] }), format: 'blocks', rev: n8.rev });
  await D.focus(p, 'm1', 'end'); await p.keyboard.type(' plus');
  b = await saved(P8, 2500);
  ok('Merge: eigene und fremde Aenderung zusammen gespeichert', b[0].data.text === 'meins plus' && b[1].data.text === 'fremd geändert', b);
  await sleep(500);
  ok('Merge: fremde Aenderung im Editor sichtbar (blockweise uebernommen)', /fremd geändert/.test(await p.textContent('[data-block-id="m2"]')));

  // 11) Offline
  await A.ctx.setOffline(true);
  await D.focus(p, 'm1', 'end'); await p.keyboard.type(' off'); await sleep(1500);
  ok('Offline: Hinweis statt Fehler', /Offline/.test(await p.textContent('#pg-state')), await p.textContent('#pg-state'));
  await A.ctx.setOffline(false); await p.evaluate(() => window.dispatchEvent(new Event('online')));
  b = await saved(P8, 2000);
  ok('Wieder online: liegengebliebenes gespeichert', /plus off$/.test(b[0].data.text), b[0]);

  // 12) Speicher nach Seitenwechseln, ein Editor je Seite
  const cdp = await A.ctx.newCDPSession(p);
  await cdp.send('Performance.enable'); await cdp.send('HeapProfiler.enable');
  async function metrics() {
    await cdp.send('HeapProfiler.collectGarbage'); await sleep(150);
    const m = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map((x) => [x.name, x.value]));
    return { kb: Math.round(m.JSHeapUsedSize / 1024), nodes: m.Nodes, lst: m.JSEventListeners };
  }
  async function go(id) { await p.evaluate((id) => { location.hash = '#' + id; }, id); for (let i = 0; i < 100; i++) { if (await p.evaluate((id) => HKPages.k.cur() && HKPages.k.cur().id === id && HKPages.k.doc.blocks().length, id)) break; await sleep(30); } await sleep(200); }
  await go(P1); await go(P6);
  const m0 = await metrics(), N = 10;
  for (let i = 0; i < N; i++) { await go(P1); await go(P6); }
  const m1 = await metrics(), per = { nodes: Math.round((m1.nodes - m0.nodes) / N), lst: Math.round((m1.lst - m0.lst) / N), kb: Math.round((m1.kb - m0.kb) / N) };
  ok('Speicher: kein Zuwachs je Seitenwechsel (' + JSON.stringify(per) + ')', per.nodes < 40 && per.lst < 25 && per.kb < 120, { m0, m1 });
  ok('Ein Editor je Seite (genau ein .ProseMirror)', (await p.evaluate(() => document.querySelectorAll('#pg-editor .ProseMirror').length)) === 1);

  // ---------------------------------------------------------------------------------------------------------------
  const csp = await p.evaluate(() => window.__csp);
  ok('CSP-Verletzungen = 0 (securitypolicyviolation)', !csp.length, csp);
  ok('Keine Seitenfehler (pageerror)', !p.errs.length, p.errs);
  await D.setEditor(p, was.tiptap ? 'tiptap' : 'editorjs');
  for (const id of [P1, P2, P3, P4, P5, P6, P7, P8, LM]) await p.api('DELETE', '/api/notes/' + id).catch(() => {});
  await br.close();
  const n = res.filter(Boolean).length;
  console.log(`\n${n}/${res.length} ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
  process.exit(n === res.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
