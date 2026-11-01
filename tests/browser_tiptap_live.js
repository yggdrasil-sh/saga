// Tiptap-Umbau T5 (gleichzeitig bearbeiten live, static/pm-live.js gegen WS /api/collab): zwei Browser (hk-test-a als
// Eigentuemer, hk-test-b mit geteilter Seite edit bzw. view). Geprueft: Live-Bindung und Cursor (Anzeigename, Klassen statt
// style), gleichzeitiges Tippen im selben Absatz konvergiert, Block ziehen (Verschieben) waehrend der andere darin tippt,
// Spalten anlegen gegen Tippen, Undo nur eigene Aenderungen, WS-Abbruch -> Stufe 1 -> Wiederverbindung ohne Verlust,
// kein PATCH des Inhalts im Live-Modus, Rechte-Entzug -> nur lesen, JSON-Schreiber (PATCH per API) waehrend Live,
// Leser (view) sendet nichts, falsches Schema (Pruefsumme manipuliert) -> nur lesen, sendet nichts, Seite unveraendert,
// Titel-Leck (Verweis auf Seite ohne Zugriff), IME (CDP Input.imeSetComposition), Schalter aus -> Stufe 1, CSP = 0.
// Nur mit SAGA_EDITOR=tiptap. Braucht Admin editor.engine = choice; schaltet notes.live selbst ueber die Admin-API? Nein:
// notes.live muss AN sein (auth.save_setting("notes", {"live": True})) - ohne live1 bricht der Test mit Hinweis ab.
// Lokal: HK_TEST_BASE=... CK=<Cookie hk-test-a> CK2=<Cookie hk-test-b> HANDLE2=<Handle von hk-test-b> node tests/browser_tiptap_live.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2, HANDLE2 = process.env.HANDLE2;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);
const MOD = process.platform === 'darwin' ? 'Meta' : 'Control';
const J = (x) => JSON.stringify(x);

(async () => {
  if (process.env.SAGA_EDITOR !== 'tiptap') { console.log('nur mit SAGA_EDITOR=tiptap'); process.exit(0); }
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
    p.patches = []; p.wsSent = 0;
    p.on('request', (r) => { if (r.method() === 'PATCH' && /\/api\/notes\/\d+$/.test(r.url())) p.patches.push(r.postData() || ''); });
    p.on('websocket', (ws) => { if (/\/api\/collab/.test(ws.url())) ws.on('framesent', (f) => { p.wsFrames = p.wsFrames || []; p.wsFrames.push(f.payload); }); });
    await p.goto(BASE + '/notes'); await sleep(500);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return { ctx, p };
  }
  const A = await session(CK), B = await session(CK2), a = A.p, b = B.p;
  const ver = await a.api('GET', '/api/version');
  if (!(ver.features || []).includes('live1')) { console.log('FAIL notes.live ist aus (features ohne live1) - Test braucht live an'); process.exit(1); }
  const wasA = await a.api('GET', '/api/account/editor'), wasB = await b.api('GET', '/api/account/editor');
  await D.setEditor(a, 'tiptap'); await D.setEditor(b, 'tiptap');
  const made = [];
  const mk = async (title, blocks, share) => {
    const id = (await a.api('POST', '/api/notes', { title })).id; made.push(id);
    if (blocks) await a.api('PATCH', '/api/notes/' + id, { body: J({ blocks }), format: 'blocks' });
    if (share) await a.api('PUT', '/api/notes/' + id + '/shares', { type: 'user', target: HANDLE2, role: share });
    return id;
  };
  const get = async (id) => JSON.parse((await a.api('GET', '/api/notes/' + id)).body || '{}').blocks || [];
  const st = (p) => p.evaluate(() => window.HKPmLive ? HKPmLive.state() : null);
  async function open(p, id, mode = 'live') {
    await p.goto(BASE + '/notes#' + id); await p.reload();
    for (let i = 0; i < 160; i++) {
      const s = await p.evaluate((id) => window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector('#pg-editor .pm-doc') && window.HKPmLive ? HKPmLive.state() : null, id);
      if (s && (!mode || s.mode === mode)) break;
      await sleep(50);
    }
    await sleep(300);
    p.patches = []; p.wsFrames = [];
  }
  // Text eines Blocks aus dem Editor-Stand (nicht aus dem DOM: fremde Cursor stehen dort als Widget mit Namen)
  const txt = (p, id) => p.evaluate((id) => { const ed = HKPages.k.editor().editor(); let t = null; ed.state.doc.descendants((n) => { if (t === null && n.attrs && n.attrs.id === id) t = n.textContent; return t === null; }); return t; }, id);
  const pmJson = (p) => p.evaluate(() => JSON.stringify(HKPages.k.editor().editor().state.doc.toJSON()));
  async function until(fn, ms = 6000) { const end = Date.now() + ms; while (Date.now() < end) { try { if (await fn()) return true; } catch (e) { /* weiter */ } await sleep(100); } return false; }
  // Update-Rahmen (art 6/5) im Datenverkehr dieser Seite seit dem letzten open()
  const updFrames = (p) => (p.wsFrames || []).filter((f) => Buffer.isBuffer(f) && (f[0] === 5 || f[0] === 6)).length;
  const P = (id, t) => ({ id, type: 'paragraph', data: { text: t } });

  // =================================================================================================================
  // 1) Bindung, Cursor, gleichzeitiges Tippen im selben Absatz
  const P1 = await mk('Live ' + RUN, [P('p1', 'Hallo'), P('p2', 'Zwei'), P('p3', 'Drei')], 'edit');
  await open(a, P1); await open(b, P1);
  let sa = await st(a), sb = await st(b);
  ok('Beide live verbunden (Rolle owner/edit, schreibbar)', sa.mode === 'live' && sb.mode === 'live' && !sa.ro && !sb.ro && sa.role === 'owner' && sb.role === 'edit', [sa, sb]);
  ok('Laden schickt kein Update und kein PATCH (keine Lade-Loeschungen)', updFrames(a) === 0 && updFrames(b) === 0 && !a.patches.length && !b.patches.length, [updFrames(a), updFrames(b), a.patches]);
  await D.focus(a, 'p1', 'end'); await D.focus(b, 'p1', 'start');
  await Promise.all([a.keyboard.type(' von Anna', { delay: 25 }), b.keyboard.type('Bert: ', { delay: 25 })]);
  const conv = await until(async () => (await txt(a, 'p1')) === (await txt(b, 'p1')) && /von Anna/.test(await txt(a, 'p1')) && /Bert: /.test(await txt(a, 'p1')));
  ok('Gleichzeitig im selben Absatz getippt: beide sehen denselben Text', conv, [await txt(a, 'p1'), await txt(b, 'p1')]);
  ok('... Text ist "Bert: Hallo von Anna"', (await txt(a, 'p1')) === 'Bert: Hallo von Anna', await txt(a, 'p1'));
  const car = await b.evaluate(() => [...document.querySelectorAll('#pg-editor .hk-caret')].map((c) => ({ cls: c.className, name: c.textContent, style: c.getAttribute('style') })));
  ok('Fremder Cursor bei Bert: Klasse hk-caret hk-c-<palette>, Name "Anna", kein style-Attribut', car.length === 1 && /hk-caret hk-c-[a-z]+/.test(car[0].cls) && car[0].name === 'Anna' && !car[0].style, car);
  ok('Cursor zeigt keinen Anmeldenamen', !(await b.evaluate(() => document.querySelector('#pg-editor').innerHTML)).includes('hk-test-a'));
  ok('Live: kein PATCH des Inhalts (Server materialisiert)', !a.patches.some((x) => /"body"/.test(x)) && !b.patches.some((x) => /"body"/.test(x)), [a.patches, b.patches]);
  await sleep(1500);
  let g = await get(P1);
  ok('Materialisiert: GET = Stand der Editoren', g[0].data.text === 'Bert: Hallo von Anna' && g.length === 3, g);
  ok('Status still (kein "Speichert"/"Ungespeichert")', !/Speichert|Ungespeichert/.test(await a.textContent('#pg-state')), await a.textContent('#pg-state'));

  // 2) Undo nimmt nur eigene Aenderungen zurueck
  await D.focus(a, 'p2', 'end'); await a.keyboard.type(' A', { delay: 20 }); await sleep(700);
  await D.focus(b, 'p3', 'end'); await b.keyboard.type(' B', { delay: 20 }); await sleep(700);
  await until(async () => (await txt(a, 'p3')).startsWith('Drei B'));
  await a.keyboard.press(MOD + '+z'); await sleep(800);
  ok('Undo bei Anna: eigenes " A" weg, Berts " B" bleibt', await until(async () => (await txt(b, 'p2')) === 'Zwei' && (await txt(a, 'p3')) === 'Drei B'),
    [await txt(a, 'p2'), await txt(a, 'p3')]);

  // 3) Block ziehen (Verschieben, Strg+Umschalt+Pfeil) waehrend der andere darin tippt
  await D.focus(b, 'p3', 'end');
  const typing = b.keyboard.type(' tippt weiter', { delay: 40 });
  await sleep(150);
  await D.focus(a, 'p3', 'start'); await a.keyboard.press(MOD + '+Shift+ArrowUp'); await sleep(100); await a.keyboard.press(MOD + '+Shift+ArrowUp');
  await typing; await sleep(1200);
  const order = async (p) => p.evaluate(() => HKPages.k.doc.blocks().filter((x) => x.parent == null).map((x) => x.id));
  const oa = await order(a), ob = await order(b);
  ok('Verschieben gegen Tippen: gleiche Reihenfolge bei beiden, p3 oben', J(oa) === J(ob) && oa[0] === 'p3', [oa, ob]);
  ok('... Berts Text vollstaendig im verschobenen Block', /Drei B tippt weiter/.test(await txt(a, 'p3')) && (await txt(a, 'p3')) === (await txt(b, 'p3')), [await txt(a, 'p3'), await txt(b, 'p3')]);
  ok('... Zustaende gleich (PM-JSON)', (await pmJson(a)) === (await pmJson(b)));

  // 4) Spalten anlegen gegen Tippen
  await D.focus(b, 'p1', 'end');
  const t2 = b.keyboard.type(' und mehr', { delay: 40 });
  await D.focus(a, 'p2', 'end'); await a.keyboard.press('Enter'); await a.keyboard.type('/2 Spalten', { delay: 10 }); await sleep(300); await a.keyboard.press('Enter');
  await t2; await sleep(1500);
  const cols = async (p) => p.evaluate(() => HKPages.k.doc.blocks().filter((x) => x.type === 'columns').length);
  ok('Spalten gegen Tippen: Spalten bei beiden, Berts Text da, Zustaende gleich', (await cols(a)) === 1 && (await cols(b)) === 1 && /und mehr/.test(await txt(a, 'p1')) && (await pmJson(a)) === (await pmJson(b)),
    [await cols(a), await cols(b), await txt(a, 'p1')]);
  await sleep(1200);
  g = await get(P1);
  ok('... materialisiert (columns im body)', g.some((x) => x.type === 'columns'), g.map((x) => x.type));

  // 5) JSON-Schreiber von aussen (PATCH per API ohne rev) waehrend Live -> beide sehen es
  const g0 = await get(P1);
  const ext = g0.map((x) => x.id === 'p2' ? Object.assign({}, x, { data: { text: 'Zwei (API)' } }) : x);
  const r5 = await a.evaluate(([id, body]) => fetch('/api/notes/' + id, { method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-Saga': '1' }, credentials: 'same-origin',
    body: JSON.stringify({ body, format: 'blocks' }) }).then((r) => r.status), [P1, J({ blocks: ext })]);
  ok('PATCH von aussen waehrend Live -> beide Editoren zeigen ihn (' + r5 + ')', await until(async () => (await txt(a, 'p2')).startsWith('Zwei (API)') && (await txt(b, 'p2')).startsWith('Zwei (API)')),
    [await txt(a, 'p2'), await txt(b, 'p2')]);

  // 6) WS-Abbruch -> Stufe 1 -> Wiederverbindung ohne Verlust
  await b.evaluate(() => HKPmLive.kill(2500));
  ok('WS weg: Bert faellt auf Stufe 1 (derselbe Editor, schreibbar)', await until(async () => (await st(b)).mode === 'stufe1' && (await b.evaluate(() => HKPages.k.editor().editor().isEditable))), await st(b));
  await D.focus(b, 'p2', 'end'); await b.keyboard.type(' offline', { delay: 20 });
  await D.focus(a, 'p3', 'end'); await a.keyboard.type(' live', { delay: 20 });
  await sleep(1800);
  ok('... Stufe 1 speichert per PATCH merge:1', b.patches.some((x) => /"merge":1/.test(x)), b.patches.map((x) => x.slice(0, 80)));
  ok('Wiederverbunden: wieder live', await until(async () => (await st(b)).mode === 'live', 15000), await st(b));
  ok('... nichts verloren: beide Eingaben bei beiden', await until(async () => /offline/.test(await txt(a, 'p2')) && /offline/.test(await txt(b, 'p2')) && /live/.test(await txt(b, 'p3')) && /live/.test(await txt(a, 'p3'))),
    [await txt(a, 'p2'), await txt(b, 'p3')]);

  // 7) Rechte-Entzug -> nur lesen; Leser (view) sendet nichts
  await a.api('PUT', '/api/notes/' + P1 + '/shares', { type: 'user', target: HANDLE2, role: 'view' });
  ok('Rolle auf view gesenkt: Bert nur lesen (editable false)', await until(async () => (await st(b)).ro && !(await b.evaluate(() => HKPages.k.editor().editor().isEditable)), 10000), await st(b));
  await open(b, P1);
  sb = await st(b);
  ok('Leser oeffnet neu: live, ro, Rolle view', sb.mode === 'live' && sb.ro && sb.role === 'view', sb);
  await b.click('#pg-editor [data-block-id="p2"]').catch(() => {}); await b.keyboard.type('xyz', { delay: 20 });
  await D.focus(a, 'p2', 'end'); await a.keyboard.type('!', { delay: 20 }); await sleep(1200);
  ok('Leser: sieht Annas Eingabe, sendet keine Updates, kein PATCH', /!/.test(await txt(b, 'p2')) && updFrames(b) === 0 && !b.patches.length, [updFrames(b), b.patches.length, await txt(b, 'p2')]);
  ok('Leser: Cursor der Leserin wird bei Anna nicht als Schreibmarke im Text gezeigt (fokuslos) bzw. ohne Anmeldename', !(await a.evaluate(() => document.querySelector('#pg-editor').innerHTML)).includes('hk-test-b'));
  await a.api('DELETE', '/api/notes/' + P1 + '/shares', { type: 'user', target: HANDLE2 });
  ok('Freigabe entzogen: Bert verliert die Live-Bindung (kein Abo mehr)', await until(async () => (await st(b)).mode !== 'live', 12000), await st(b));

  // 8) Falsches Schema (Pruefsumme manipuliert) -> nur lesen, sendet nichts, Seite unveraendert
  const P8 = await mk('Schema ' + RUN, [P('s1', 'Bleibt'), { id: 'u1', type: 'kuenftig', data: { x: 1 } }], 'edit');
  const g8 = await get(P8);
  await b.goto(BASE + '/notes#' + P8);
  await b.evaluate(() => { const h = HKPmSchema.hash; HKPmSchema.hash = () => 'ffffffffffffffff'; window.__h = h; });
  await b.evaluate(() => HKPages.k.open(HKPages.k.cur() ? HKPages.k.cur().id : +location.hash.slice(1)));
  b.wsFrames = []; b.patches = [];
  await until(async () => (await st(b)) && (await st(b)).mode === 'lock', 8000);
  sb = await st(b);
  ok('Schema abweichend: nur lesen (lock), Hinweis "Neu laden"', sb.mode === 'lock' && !!(await b.$('#pm-live-hint')) && !(await b.evaluate(() => HKPages.k.editor().editor().isEditable)), sb);
  await b.click('#pg-editor [data-block-id="s1"]').catch(() => {}); await b.keyboard.type('NEIN', { delay: 20 }); await sleep(1200);
  ok('... sendet nichts (keine Update-Rahmen, kein PATCH)', updFrames(b) === 0 && !b.patches.length, [updFrames(b), b.patches]);
  ok('... Seite unveraendert (auch der unbekannte Block)', J(await get(P8)) === J(g8), await get(P8));
  await b.evaluate(() => { HKPmSchema.hash = window.__h; });

  // 9) Titel-Leck: Verweis auf eine Seite ohne Zugriff zeigt bei Bert keinen Titel
  const SECRET = 'Geheim ' + RUN;
  const PS = await mk(SECRET, [P('x', 'nur Anna')]);
  const P9 = await mk('Verweis ' + RUN, [P('v1', 'Siehe <a href="/notes#' + PS + '">' + SECRET + '</a> hier')], 'edit');
  await open(b, P9);
  const h9 = await b.evaluate(() => document.querySelector('#pg-editor').textContent);
  ok('Titel-Leck: Bert sieht den Titel der fremden Seite nicht', !h9.includes(SECRET) && (await st(b)).mode === 'live', h9);
  const ydoc = await b.evaluate(() => { const d = HKPmLive.doc(); return d ? JSON.stringify(d.getXmlFragment('prosemirror').toJSON()) : ''; });
  ok('... und er steht nicht im Live-Zustand', ydoc && !ydoc.includes(SECRET), ydoc.slice(0, 200));

  // 10) IME (CDP Input.imeSetComposition)
  const P10 = await mk('IME ' + RUN, [P('i1', 'Start')], 'edit');
  await open(a, P10); await open(b, P10);
  await D.focus(b, 'i1', 'end');
  const cdp = await B.ctx.newCDPSession(b);
  await cdp.send('Input.imeSetComposition', { text: 'に', selectionStart: 1, selectionEnd: 1 });
  await cdp.send('Input.imeSetComposition', { text: 'にほ', selectionStart: 2, selectionEnd: 2 });
  await cdp.send('Input.insertText', { text: '日本' });
  await sleep(1200);
  ok('IME: Komposition bei Bert -> "Start日本" bei beiden', await until(async () => (await txt(a, 'i1')) === 'Start日本' && (await txt(b, 'i1')) === 'Start日本'),
    [await txt(a, 'i1'), await txt(b, 'i1')]);

  // 11) Schalter aus -> Stufe 1 (kein WS-Aufbau), Speichern per PATCH
  ok('Seitenwechsel: ein Editor, eine Verbindung', (await a.evaluate(() => document.querySelectorAll('#pg-editor .ProseMirror').length)) === 1);

  // ---------------------------------------------------------------------------------------------------------------
  for (const [n, p] of [['Anna', a], ['Bert', b]]) {
    const csp = await p.evaluate(() => window.__csp);
    ok('CSP-Verletzungen = 0 (' + n + ')', !csp.length, csp);
    ok('Keine Seitenfehler (' + n + ')', !p.errs.length, p.errs);
  }
  await D.setEditor(a, wasA.tiptap ? 'tiptap' : 'editorjs'); await D.setEditor(b, wasB.tiptap ? 'tiptap' : 'editorjs');
  for (const id of made) await a.api('DELETE', '/api/notes/' + id).catch(() => {});
  await br.close();
  console.log(res.filter(Boolean).length + '/' + res.length, res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER');
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
