// Tiptap-Umbau T4b: Daten-Werkzeuge (Datenbank-Ansicht, Aufgaben, Kalender, Widget, Schaltflaechen) als Node-Views
// ueber static/pm-tools.js + static/pm-tools-daten.js - ohne Kern-Editor (T2): ein minimaler Tiptap-Editor (nur
// Saga-Schema + nodeViews) auf der eingeloggten /notes-Seite, Dokument aus echten Bloecken einer Seite.
// Abnahmekarten (Konzept 4.3, soweit ohne Kern-Editor): rendert wie Editor.js, Bedienung = genau eine Transaktion mit
// neuem data, Tasten/Ziehen/Einfuegen bleiben im Werkzeug (stopEvent), Rolle Lesen/fremde Seite, update(data) von
// aussen (offenes Widget-Panel bleibt), destroy ohne Lecks (Intervalle, Listener), Regel 0 (unbekannte Felder),
// Signaturen der Schaltflaechen bleiben, Rueckweg blocksFromDoc byte-gleich ohne Bedienung, keine CSP-Verletzung.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> CK2=<Cookie hk-test-b> node tests/browser_tiptap_daten.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + String(JSON.stringify(i)).slice(0, 1500))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(120); } }
const SCRIPTS = ['vendor/tiptap/saga-pm.js', 'pm-schema.js', 'pm-adapter.js', 'pm-tools.js', 'pm-tools-daten.js'];

// Im Browser: Mini-Editor bauen (nur Schema + Node-Views), Transaktionen mitschreiben, Hilfen fuer die Pruefungen
function installHelpers() {
  // Intervalle und Listener mitzaehlen (fuer die Leck-Pruefung)
  const live = new Set(), si = window.setInterval, ci = window.clearInterval;
  window.setInterval = function (f, ms) { const id = si.apply(window, arguments); live.add(id); return id; };
  window.clearInterval = function (id) { live.delete(id); return ci.call(window, id); };
  const L = { n: 0 };
  [document, window].forEach((t) => {
    const a = t.addEventListener, r = t.removeEventListener;
    t.addEventListener = function () { L.n++; return a.apply(t, arguments); };
    t.removeEventListener = function () { L.n--; return r.apply(t, arguments); };
  });
  window.__T4 = {
    live, L,
    mk(blocks, base) {
      const host = document.createElement('div'); host.id = 't4b'; host.className = 'page-body';
      const anchor = document.getElementById('pg-editor');
      anchor.parentNode.insertBefore(host, anchor.nextSibling);
      const T = Object.assign({}, window.SagaPM.core, { UniqueID: window.SagaPM.UniqueID });
      const ext = window.HKPmSchema.extensions(T, { uniqueId: false, nodeViews: window.HKTools.nodeViews(base || {}) })
        .concat([window.SagaPM.UndoRedo]);
      const save = { blocks };
      const orig = new WeakMap(), pm = window.HKPmAdapter.docFromBlocks(save, { orig });
      const log = [];
      const ed = new window.SagaPM.core.Editor({ element: host, injectCSS: false, extensions: ext, content: pm });
      window.HKPmAdapter.bind(ed.state.doc, save, { orig });
      ed.on('transaction', ({ transaction: tr }) => {
        if (!tr.docChanged) return;
        log.push({ tool: tr.getMeta('hkTool') || null, remote: !!tr.getMeta('remote'), hist: tr.getMeta('addToHistory') !== false });
      });
      window.__E = { ed, host, log, orig, save };
      return true;
    },
    kill() { const E = window.__E; if (!E) return; E.ed.destroy(); E.host.remove(); window.__E = null; },
    pos(id) { let at = null; window.__E.ed.state.doc.forEach((n, off) => { if (n.attrs.id === id) at = off; }); return at; },
    data(id) { const E = window.__E, p = this.pos(id); return p === null ? null : E.ed.state.doc.nodeAt(p).attrs.data; },
    remote(id, d) {                       // Aenderung von aussen (Live/Undo/Rebase): neue data als eigene Transaktion
      const E = window.__E, p = this.pos(id), n = E.ed.state.doc.nodeAt(p);
      E.ed.view.dispatch(E.ed.state.tr.setNodeMarkup(p, null, Object.assign({}, n.attrs, { data: d })).setMeta('remote', true).setMeta('addToHistory', false));
    },
    back() { const E = window.__E; return window.HKPmAdapter.blocksFromDoc(E.ed.state.doc, { orig: E.orig }).blocks; },
    tool(id) { return document.querySelector('#t4b [data-block-id="' + id + '"]'); },
    sig(root) {                           // Darstellung vergleichen: Wurzelklasse + Text (Ziffern/Uhrzeit ausgeblendet)
      const r = root && root.querySelector('.embed-db, .embed-tasks, .embed-cal, .wg-blk, .btnrow');
      return r ? r.className.replace(/\s*(owner-only|wg-editing)\b/g, '') + '|' + r.textContent.replace(/\d+/g, '#').replace(/\s+/g, ' ').trim() : null;
    }
  };
}

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const host = new URL(BASE).hostname;
  const ctxB = await br.newContext({ locale: 'de-DE' });
  await ctxB.addCookies([{ name: 'saga', value: CK2, domain: host, path: '/' }]);
  const pb = await ctxB.newPage(); await pb.goto(BASE + '/notes');
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: host, path: '/' }]);
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused/.test(m.text())) errs.push(m.text()); });
  await p.addInitScript(() => {
    window.__csp = [];
    document.addEventListener('securitypolicyviolation', (e) => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));
  });
  await p.goto(BASE + '/notes');
  const mkApi = (pg) => (m, url, b) => pg.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const api = mkApi(p), apiB = mkApi(pb);
  const body = async (a, id) => JSON.parse((await a('GET', '/api/notes/' + id)).body || '{"blocks":[]}').blocks;

  // --- Daten: Datenbank mit zwei Ansichten, Seite mit allen fuenf Werkzeugen -------------------------------------------
  const db = (await api('POST', '/api/notes', { title: 'T4b Projekte', kind: 'database' })).id;
  const VIEWS = [{ id: 'alle', name: 'Alle', type: 'table' }, { id: 'liste', name: 'Liste', type: 'list' }];
  await api('PATCH', '/api/notes/' + db, { schema: { props: [{ id: 'std', name: 'Stunden', type: 'number' }], views: VIEWS } });
  for (const [t, h] of [['Alpha', 2], ['Beta', 3]]) await api('POST', '/api/notes', { title: t, parent_id: db, props: { std: h } });
  await api('POST', '/api/tasks', { text: 'T4b Aufgabe eins' });
  const page = (await api('POST', '/api/notes', { title: 'T4b Werkzeuge' })).id;
  const B0 = [
    { id: 'p1', type: 'paragraph', data: { text: 'Vorher' } },
    { id: 'dbv1', type: 'database', data: { db, view: 'alle', views: VIEWS } },
    { id: 'tsk1', type: 'tasks', data: {} },
    { id: 'cal1', type: 'calendar', data: { view: 'month', days: 14 } },
    { id: 'wclk', type: 'widget', data: { kind: 'clock', v: 1, title: 'Uhr', cfg: { tz: '', h24: true, sec: true, analog: false } } },
    { id: 'wmet', type: 'widget', data: { kind: 'metric', v: 1, title: 'Zeilen', cfg: { db, view: '', agg: 'count', prop: '', fmt: 'num' } } },
    { id: 'btn1', type: 'buttons', data: { buttons: [{ label: 'Neue Aufgabe', icon: '✓', action: 'task', target: null, tpl: null, title: '', open: '' }] } },
    { id: 'p2', type: 'paragraph', data: { text: 'Nachher' } }
  ];
  await api('PATCH', '/api/notes/' + page, { body: JSON.stringify({ blocks: B0 }), format: 'blocks' });
  const saved = await body(api, page);
  ok('Seite mit 8 Bloecken gespeichert (Server-Form)', saved.length === 8 && saved[1].type === 'database', saved.map((b) => b.type));

  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + page);
  await p.waitForSelector('#pg-editor [data-block-id="dbv1"] .embed-db table.db-table');
  await until(() => p.evaluate(() => !!document.querySelector('#pg-editor .embed-tasks .embed-head')));
  await sleep(800);
  for (const s of SCRIPTS) await p.addScriptTag({ url: '/static/' + s });
  await p.addStyleTag({ url: '/static/vendor/tiptap/saga-pm.css' });
  const reg = await p.evaluate(() => window.HKTools.types().sort().join(','));
  ok('HKTools: database, tasks, calendar, widget, buttons registriert', reg === 'buttons,calendar,database,tasks,widget', reg);
  const sl = await p.evaluate(() => window.HKTools.slash().map((x) => x.type + ':' + x.title));
  ok(`Slash-Eintraege aus den Werkzeugen (${sl.length})`, sl.length === 13 && sl.some((x) => x === 'widget:Widget: Uhr') && sl.indexOf('database:Datenbank') >= 0, sl);
  await p.evaluate(installHelpers);

  // Regel 0: unbekannte Felder zusaetzlich im Dokument (der Server kennt sie nicht, ein neuerer Client schon)
  const blocks = JSON.parse(JSON.stringify(saved));
  blocks[1].data.zukunft = { x: 1 }; blocks[2].data.neu = 'ja'; blocks[3].data.farbe = 'blau';
  blocks[5].data.extra2 = [1, 2]; blocks[6].data.layout = 'reihe';
  blocks[6].data.buttons[0].sig = 'SIG-ALT'; blocks[6].data.buttons[0].zz = 7;
  const snap = JSON.stringify(blocks);
  await p.evaluate((b) => window.__T4.mk(b), blocks);
  await until(() => p.evaluate(() => !!document.querySelector('#t4b .embed-db table.db-table') && !!document.querySelector('#t4b .embed-tasks .embed-head')
    && /\d/.test((document.querySelector('#t4b [data-block-id="wmet"] .wg-v') || {}).textContent || '')), 8000);
  await sleep(1200);

  // --- Darstellung wie Editor.js -------------------------------------------------------------------------------------
  const cmp = await p.evaluate(() => ['dbv1', 'tsk1', 'cal1', 'wclk', 'wmet', 'btn1'].map((id) => {
    const a = window.__T4.sig(document.querySelector('#pg-editor [data-block-id="' + id + '"]')), b = window.__T4.sig(window.__T4.tool(id));
    return [id, a === b && !!a, a, b];
  }));
  cmp.forEach((c) => ok(`Node-View ${c[0]} rendert wie Editor.js`, c[1], c.slice(2)));
  const nv = await p.evaluate(() => [...document.querySelectorAll('#t4b .hk-tool')].map((x) => [x.getAttribute('data-block-type'), x.getAttribute('data-block-id'), x.getAttribute('contenteditable')]));
  ok('Node-View-Huelle: data-block-type/-id, contenteditable=false', nv.length === 6 && nv.every((x) => x[1] && x[2] === 'false'), nv);
  ok('Metrik-Widget zeigt den Serverwert (2 Zeilen)', await p.evaluate(() => document.querySelector('#t4b [data-block-id="wmet"] .wg-v').textContent) === '2');
  ok('Kein Speichern beim Einhaengen (0 Transaktionen)', (await p.evaluate(() => window.__E.log.length)) === 0, await p.evaluate(() => window.__E.log));
  const rt = await p.evaluate(() => JSON.stringify(window.__T4.back()));
  ok('Rueckweg blocksFromDoc byte-gleich (ohne Bedienung, inkl. unbekannter Felder und sig)', rt === snap, [rt.slice(0, 400), snap.slice(0, 400)]);

  // --- Datenbank: Ansicht wechseln = 1 Transaktion, Zeile anlegen = keine (andere Seite) -----------------------------------
  await p.click('#t4b [data-block-id="dbv1"] .db-tab:not(.on):not(.db-tab-add)');
  await until(() => p.evaluate(() => window.__E.log.length > 0));
  await sleep(300);
  let lg = await p.evaluate(() => window.__E.log);
  let d = await p.evaluate(() => window.__T4.data('dbv1'));
  ok('DB: Ansicht wechseln -> genau eine Transaktion (hkTool database)', lg.length === 1 && lg[0].tool === 'database' && lg[0].hist, lg);
  ok('DB: neues data.view, Regel 0 (zukunft bleibt), views unveraendert', d.view === 'liste' && d.zukunft && d.zukunft.x === 1 && d.views.length === 2, d);
  const others = await p.evaluate(() => { const b = window.__T4.back(); return JSON.stringify(b.filter((x) => x.id !== 'dbv1')); });
  ok('Andere Bloecke bleiben byte-gleich', others === JSON.stringify(blocks.filter((x) => x.id !== 'dbv1')));
  const rows0 = (await api('GET', '/api/notes/' + db + '/rows')).rows.length;
  await p.click('#t4b [data-block-id="dbv1"] .db-new-top');
  await until(async () => (await api('GET', '/api/notes/' + db + '/rows')).rows.length === rows0 + 1);
  await sleep(500);
  ok('DB: Zeile anlegen legt die Zeile an (Server)', (await api('GET', '/api/notes/' + db + '/rows')).rows.length === rows0 + 1);
  ok('DB: Zeile anlegen aendert das Dokument nicht', (await p.evaluate(() => window.__E.log.length)) === 1, await p.evaluate(() => window.__E.log));
  await p.keyboard.press('Escape'); await sleep(200);

  // --- Tasten/Ziehen/Einfuegen bleiben im Werkzeug ----------------------------------------------------------------------
  await p.click('#t4b [data-block-id="dbv1"] .db-tab:not(.on):not(.db-tab-add)');      // zurueck zur Tabelle
  await until(() => p.evaluate(() => !!document.querySelector('#t4b [data-block-id="dbv1"] .db-title-in')));
  const docBefore = await p.evaluate(() => JSON.stringify(window.__E.ed.getJSON()));
  const n0 = await p.evaluate(() => window.__E.log.length);
  const inp = p.locator('#t4b [data-block-id="dbv1"] .db-title-in').first();
  await inp.click(); await p.keyboard.press('End'); await p.keyboard.type('X'); await p.keyboard.press('Backspace');
  await p.keyboard.press('Enter'); await sleep(300); await p.keyboard.press('Escape');
  const keyRes = await p.evaluate(() => {
    const E = window.__E, out = {};
    const box = document.querySelector('#t4b [data-block-id="dbv1"] .embed-db');
    const dt = new DataTransfer(); dt.setData('text/plain', 'Eingefuegt'); dt.setData('text/html', '<p>Eingefuegt</p>');
    const pe = new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt });
    box.querySelector('.db-title-in').dispatchEvent(pe);
    const de = new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt });
    box.dispatchEvent(de);
    // Schaltflaechen-Leiste hat kein isolate(): hier wirkt nur stopEvent der Node-View
    const b = document.querySelector('#t4b [data-block-id="btn1"] .nbtn');
    b.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true, cancelable: true }));
    b.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    out.doc = JSON.stringify(E.ed.getJSON());
    return out;
  });
  ok('Enter/Backspace/Tippen in der DB-Zelle: Dokument unveraendert', keyRes.doc === docBefore);
  ok('Einfuegen und Drop im Werkzeug: keine Transaktion', (await p.evaluate(() => window.__E.log.length)) === n0,
    await p.evaluate(() => window.__E.log));
  const stopInfo = await p.evaluate(() => {
    const dom = document.querySelector('#t4b [data-block-id="btn1"]'), desc = dom.pmViewDesc, inner = dom.querySelector('.nbtn');
    const e1 = new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }); Object.defineProperty(e1, 'target', { value: inner });
    const e2 = new MouseEvent('mousedown', { bubbles: true }); Object.defineProperty(e2, 'target', { value: dom });
    return { inner: desc.stopEvent(e1), shell: desc.stopEvent(e2) };
  });
  ok('stopEvent: Ereignis im Werkzeug -> true, Rand der Huelle -> false (Block waehlbar)', stopInfo.inner === true && stopInfo.shell === false, stopInfo);
  const back1 = await p.evaluate(() => window.__T4.data('dbv1'));
  ok('DB nach Tasten wieder Tabelle (2. Transaktion), sonst nichts', back1.view === 'alle' && (await p.evaluate(() => window.__E.log.length)) === 2, [back1.view, await p.evaluate(() => window.__E.log)]);

  // --- Kalender -------------------------------------------------------------------------------------------------------
  await p.click('#t4b [data-block-id="cal1"] .cal-mode-b:not(.on)');
  await sleep(300);
  lg = await p.evaluate(() => window.__E.log.slice(2));
  d = await p.evaluate(() => window.__T4.data('cal1'));
  ok('Kalender: Liste -> eine Transaktion, view=list, Regel 0 (farbe)', lg.length === 1 && lg[0].tool === 'calendar' && d.view === 'list' && d.days === 14 && d.farbe === 'blau', [lg, d]);

  // --- Schaltflaechen: + = eine Transaktion, Signatur und unbekannte Felder bleiben ----------------------------------------
  await p.click('#t4b [data-block-id="btn1"] .nbtn-add');
  await sleep(300);
  lg = await p.evaluate(() => window.__E.log.slice(3));
  d = await p.evaluate(() => window.__T4.data('btn1'));
  ok('Schaltflaechen: + -> eine Transaktion, zwei Schaltflaechen', lg.length === 1 && lg[0].tool === 'buttons' && d.buttons.length === 2, [lg, d]);
  ok('Schaltflaechen: sig und unbekanntes Feld der ersten bleiben, layout bleibt', d.buttons[0].sig === 'SIG-ALT' && d.buttons[0].zz === 7 && d.layout === 'reihe' && !('sig' in d.buttons[1]), d);
  ok('Schaltflaechen gerendert: 2 + Hinzufuegen', await p.locator('#t4b [data-block-id="btn1"] .nbtn').count() === 2);

  // --- Widget: Panel, Aenderung = eine Transaktion, update() von aussen laesst das Panel offen ------------------------------
  await p.click('#t4b [data-block-id="wclk"] .wg');
  await until(() => p.evaluate(() => !!document.querySelector('.wg-panel')));
  const inPanel = await p.evaluate(() => !!document.querySelector('#t4b [data-block-id="wclk"] .wg-panel'));
  ok('Widget: Klick oeffnet das Einrichten-Panel im Block', inPanel);
  const tIn = p.locator('#t4b .wg-panel .wg-row', { has: p.locator('.wg-l', { hasText: /^Titel$/ }) }).locator('input');
  const nW = await p.evaluate(() => window.__E.log.length);
  await tIn.fill('Weltzeit'); await sleep(300);
  lg = await p.evaluate((n) => window.__E.log.slice(n), nW);
  d = await p.evaluate(() => window.__T4.data('wclk'));
  ok('Widget: Titel aendern -> eine Transaktion, data.title neu', lg.length === 1 && lg[0].tool === 'widget' && d.title === 'Weltzeit' && d.cfg.sec === true, [lg, d]);
  await p.evaluate(() => { const d = JSON.parse(JSON.stringify(window.__T4.data('wclk'))); d.title = 'Von fern'; d.cfg.h24 = false; d.neu3 = 1; window.__T4.remote('wclk', d); });
  await sleep(300);
  const up = await p.evaluate(() => ({
    panel: !!document.querySelector('#t4b [data-block-id="wclk"] .wg-panel'),
    t: (document.querySelector('#t4b [data-block-id="wclk"] .wg-t') || {}).textContent,
    inp: [...document.querySelectorAll('#t4b [data-block-id="wclk"] .wg-panel input')].map((x) => x.value).join('|'),
    n: window.__E.log.length
  }));
  ok('Widget update(data): Panel bleibt offen, Titel und Felder neu', up.panel && up.t === 'Von fern' && /Von fern/.test(up.inp), up);
  await p.click('#t4b [data-block-id="wclk"] .wg-done'); await sleep(300);
  d = await p.evaluate(() => window.__T4.data('wclk'));
  ok('Widget: Fertig ohne Aenderung -> keine weitere Transaktion, neu3 (Regel 0) bleibt', (await p.evaluate(() => window.__E.log.length)) === up.n && d.neu3 === 1, d);

  // --- update(data) von aussen fuer DB, Kalender, Schaltflaechen --------------------------------------------------------
  await p.evaluate(() => {
    const T = window.__T4;
    const a = JSON.parse(JSON.stringify(T.data('dbv1'))); a.view = 'liste'; T.remote('dbv1', a);
    const c = JSON.parse(JSON.stringify(T.data('cal1'))); c.view = 'month'; T.remote('cal1', c);
    const b = JSON.parse(JSON.stringify(T.data('btn1'))); b.buttons.push({ label: 'Drei', icon: '', action: '', target: null, tpl: null, title: '', open: '' }); T.remote('btn1', b);
  });
  await until(() => p.evaluate(() => /Liste/.test((document.querySelector('#t4b [data-block-id="dbv1"] .db-tab.on') || {}).textContent || '')), 5000);
  const ext = await p.evaluate(() => ({
    db: (document.querySelector('#t4b [data-block-id="dbv1"] .db-tab.on') || {}).textContent,
    cal: (document.querySelector('#t4b [data-block-id="cal1"] .cal-mode-b.on') || {}).textContent,
    btn: document.querySelectorAll('#t4b [data-block-id="btn1"] .nbtn').length,
    own: window.__E.log.filter((x) => !x.remote).length
  }));
  ok('update(data): DB zeigt die neue Ansicht, Kalender Monat, 3 Schaltflaechen', /Liste/.test(ext.db) && /Monat/.test(ext.cal) && ext.btn === 3, ext);
  ok('update(data) erzeugt keine eigene Transaktion zurueck', ext.own === 5, ext);

  // Gegenprobe zu stopEvent: Block gewaehlt, Backspace IM Werkzeug laesst ihn stehen, an der Huelle loescht ProseMirror ihn
  const ctl = await p.evaluate(() => {
    const E = window.__E, T = window.__T4, S = window.SagaPM.pm.state, ed = E.ed;
    const sel = () => ed.view.dispatch(ed.state.tr.setSelection(S.NodeSelection.create(ed.state.doc, T.pos('btn1'))));
    const bs = (t) => t.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', code: 'Backspace', keyCode: 8, bubbles: true, cancelable: true }));
    sel(); bs(document.querySelector('#t4b [data-block-id="btn1"] .nbtn'));
    const inTool = T.pos('btn1') !== null;
    sel(); bs(document.querySelector('#t4b [data-block-id="btn1"]'));
    return { inTool, shell: T.pos('btn1') === null };
  });
  ok('Gegenprobe: Backspace im Werkzeug ignoriert, an der Huelle loescht ProseMirror den Block', ctl.inTool && ctl.shell, ctl);

  // Undo/Redo: die Werkzeug-Transaktion ist ein Schritt; Rueckgaengig zeichnet ueber update(data) neu
  await p.evaluate(() => window.__E.ed.commands.undo()); await sleep(200);
  ok('Undo stellt den geloeschten Schaltflaechen-Block wieder her', await p.locator('#t4b [data-block-id="btn1"] .nbtn').count() === 3);
  await sleep(600);
  await p.click('#t4b [data-block-id="cal1"] .cal-mode-b:not(.on)'); await sleep(300);
  const u1 = await p.evaluate(() => window.__T4.data('cal1').view);
  await p.evaluate(() => window.__E.ed.commands.undo()); await sleep(300);
  const u2 = await p.evaluate(() => [window.__T4.data('cal1').view, (document.querySelector('#t4b [data-block-id="cal1"] .cal-mode-b.on') || {}).textContent]);
  await p.evaluate(() => window.__E.ed.commands.redo()); await sleep(300);
  const u3 = await p.evaluate(() => [window.__T4.data('cal1').view, (document.querySelector('#t4b [data-block-id="cal1"] .cal-mode-b.on') || {}).textContent]);
  ok('Undo/Redo der Kalender-Ansicht: ein Schritt, Node-View folgt', u1 === 'list' && u2[0] === 'month' && /Monat/.test(u2[1]) && u3[0] === 'list' && /Liste/.test(u3[1]), [u1, u2, u3]);

  // --- Nur lesen (Editor/ctx): Bedienung schreibt nichts ins Dokument -----------------------------------------------------
  await p.evaluate(() => window.__T4.kill());
  await p.evaluate((b) => window.__T4.mk(b, { readOnly: true }), blocks);
  await until(() => p.evaluate(() => !!document.querySelector('#t4b [data-block-id="cal1"] .cal-mode-b:not(.on)')));
  await p.click('#t4b [data-block-id="cal1"] .cal-mode-b:not(.on)'); await sleep(200);
  await p.click('#t4b [data-block-id="wclk"] .wg'); await sleep(300);
  const roRes = await p.evaluate(() => ({ log: window.__E.log.length, panel: !!document.querySelector('#t4b .wg-panel') }));
  ok('readOnly: Kalender umschalten schreibt nicht, Widget oeffnet kein Panel', roRes.log === 0 && !roRes.panel, roRes);
  await p.evaluate(() => window.__T4.kill());

  // --- destroy ohne Lecks: Intervalle (Uhr: jede Sekunde), Listener an document/window ---------------------------------------
  const leak = await p.evaluate(async (b) => {
    const T = window.__T4, before = new Set(T.live), l0 = T.L.n, out = [];
    for (let i = 0; i < 3; i++) {
      T.mk(b); await new Promise((r) => setTimeout(r, 600));
      out.push([...T.live].filter((x) => !before.has(x)).length);
      T.kill(); await new Promise((r) => setTimeout(r, 100));
    }
    await new Promise((r) => setTimeout(r, 1200));
    return { during: out, after: [...T.live].filter((x) => !before.has(x)).length, listeners: T.L.n - l0, hk: document.querySelectorAll('.hk-tool').length };
  }, blocks);
  ok('destroy: Uhr-Intervall laeuft waehrend, danach keines mehr', leak.during.every((x) => x >= 1) && leak.after === 0, leak);
  ok('destroy: Listener an document/window ausgeglichen, keine Node-Views uebrig', leak.listeners <= 0 && leak.hk === 0, leak);

  // --- Fremde Seite mit Rolle Lesen ---------------------------------------------------------------------------------------
  const dbB = (await apiB('POST', '/api/notes', { title: 'T4b fremde DB', kind: 'database' })).id;
  const pageB = (await apiB('POST', '/api/notes', { title: 'T4b fremd' })).id;
  const BB = [
    { id: 'fdb', type: 'database', data: { db: dbB, view: null, views: null } },
    { id: 'ftk', type: 'tasks', data: {} }, { id: 'fcal', type: 'calendar', data: { view: 'month', days: 14 } },
    { id: 'fwg', type: 'widget', data: { kind: 'clock', v: 1, cfg: { tz: '', h24: true, sec: false, analog: false } } },
    { id: 'fbt', type: 'buttons', data: { buttons: [{ label: 'Eintrag', icon: '', action: 'db', target: dbB, tpl: null, title: '', open: '' }] } }
  ];
  await apiB('PATCH', '/api/notes/' + pageB, { body: JSON.stringify({ blocks: BB }), format: 'blocks' });
  await apiB('PUT', '/api/notes/' + pageB + '/shares', { type: 'user', target: 'hk-test-a', role: 'view' });
  const fb = await body(apiB, pageB);
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + pageB);
  await p.waitForSelector('#pg-editor [data-block-id="fbt"] .nbtn'); await sleep(1000);
  for (const s of SCRIPTS) await p.addScriptTag({ url: '/static/' + s });
  await p.evaluate(installHelpers);
  await p.evaluate((b) => window.__T4.mk(b), fb);
  await until(() => p.evaluate(() => document.querySelectorAll('#t4b .hk-tool').length === 5));
  await sleep(800);
  const fr = await p.evaluate(() => {
    const q = (id, s) => document.querySelector('#t4b [data-block-id="' + id + '"] ' + s);
    return {
      tasks: !!q('ftk', '.owner-only'), cal: !!q('fcal', '.owner-only'),
      btnOff: !!q('fbt', '.nbtn.nbtn-off'), btnAdd: !!q('fbt', '.nbtn-add'), btnCfg: !!q('fbt', '.nbtn-cfg'),
      wgEd: !!q('fwg', '.wg.wg-ed'), db: (q('fdb', '.embed-db') || {}).textContent
    };
  });
  const frCmp = await p.evaluate(() => ['fdb', 'ftk', 'fcal', 'fwg', 'fbt'].map((id) => window.__T4.sig(document.querySelector('#pg-editor [data-block-id="' + id + '"]')) === window.__T4.sig(window.__T4.tool(id))));
  ok('Fremde Seite: Aufgaben/Kalender nur fuer den Eigentuemer', fr.tasks && fr.cal, fr);
  ok('Rolle Lesen: Schaltflaeche ausgegraut, kein Einrichten/Hinzufuegen, Widget nicht bearbeitbar', fr.btnOff && !fr.btnAdd && !fr.btnCfg && !fr.wgEd, fr);
  ok('Fremde Seite: alle fuenf Node-Views wie Editor.js', frCmp.every(Boolean), frCmp);
  await p.click('#t4b [data-block-id="fwg"] .wg'); await sleep(300);
  ok('Rolle Lesen: keine Transaktion, Rueckweg byte-gleich', (await p.evaluate(() => window.__E.log.length)) === 0
    && (await p.evaluate(() => JSON.stringify(window.__T4.back()))) === JSON.stringify(fb));
  await p.evaluate(() => window.__T4.kill());

  const v = await p.evaluate(() => window.__csp);
  ok('Keine CSP-Verletzung', !v.length, v);
  ok('Keine Seitenfehler', !errs.length, errs);
  await apiB('DELETE', '/api/notes/' + pageB); await apiB('DELETE', '/api/notes/' + dbB);
  await api('DELETE', '/api/notes/' + page); await api('DELETE', '/api/notes/' + db);
  await br.close();
  console.log('T4b Daten-Werkzeuge ' + res.filter(Boolean).length + '/' + res.length + (res.every(Boolean) ? ' ALLES GRUEN' : ''));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.stack || e.message); process.exit(1); });
