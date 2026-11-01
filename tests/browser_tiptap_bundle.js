// T1 (Tiptap-Umbau): vorgebaute Bundles static/vendor/tiptap/ laufen unter der Saga-CSP.
// Prueft: Auslieferung durch den Server, Editor mit StarterKit + injectCSS:false (kein <style>), Tippen + getJSON,
// UniqueID, Live-Bundle ohne zweite yjs-/ProseMirror-Instanz (Collaboration + Y-Update von einem zweiten Y.Doc,
// DragHandle im Live-Editor), keine CSP-Verletzung, kein eval.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<signiertes Sitzungs-Cookie> node tests/browser_tiptap_bundle.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1100, height: 800 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [], cons = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') cons.push(m.text()); });
  await p.addInitScript(() => {
    window.__csp = [];
    document.addEventListener('securitypolicyviolation', (e) => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));
  });
  const resp = await p.goto(BASE + '/notes');
  const csp = resp.headers()['content-security-policy'] || '';
  ok('Seite traegt Saga-CSP (script-src/style-src self, kein unsafe-eval)', /script-src 'self'/.test(csp) && /style-src 'self'/.test(csp) && !/unsafe-eval/.test(csp), csp);
  await p.waitForTimeout(500);

  const files = await p.evaluate(async () => {
    const out = {};
    for (const f of ['saga-pm.js', 'saga-y.js', 'saga-pm.css']) {
      const r = await fetch('/static/vendor/tiptap/' + f); const t = await r.text();
      out[f] = { st: r.status, ct: r.headers.get('content-type'), len: t.length, evalish: /\beval\(|new Function\(/.test(t) };
    }
    return out;
  });
  ok('Server liefert saga-pm.js/saga-y.js/saga-pm.css aus', Object.values(files).every((f) => f.st === 200 && f.len > 1000), files);
  ok('Kein eval/new Function in den Bundles', Object.values(files).every((f) => !f.evalish), files);

  const styles0 = await p.evaluate(() => document.querySelectorAll('style').length);
  await p.addStyleTag({ url: '/static/vendor/tiptap/saga-pm.css' });
  await p.addScriptTag({ url: '/static/vendor/tiptap/saga-pm.js' });
  const api = await p.evaluate(() => {
    const S = window.SagaPM, need = ['core', 'pm', 'StarterKit', 'UniqueID', 'DragHandle', 'NodeRange', 'Suggestion', 'Placeholder', 'TrailingNode', 'BubbleMenu', 'BubbleMenuPlugin', 'version'];
    const pm = ['model', 'state', 'view', 'transform', 'commands', 'keymap', 'history'];
    return { missing: need.filter((k) => !S || !S[k]).concat(pm.filter((k) => !S || !S.pm[k]).map((k) => 'pm.' + k)),
      core: S && ['Editor', 'Node', 'Mark', 'Extension', 'mergeAttributes', 'getSchema'].every((k) => S.core[k]), version: S && S.version, y: !!window.Y };
  });
  ok('SagaPM erfuellt den Exportvertrag', !api.missing.length && api.core && /^3\./.test(api.version), api);

  // Editor mit StarterKit + injectCSS:false
  const ed = await p.evaluate(() => {
    const S = window.SagaPM, d = document.createElement('div'); d.id = 'tt-test'; document.body.prepend(d);
    window.__ed = new S.core.Editor({ element: d, injectCSS: false, content: '<p>Hallo</p>',
      extensions: [S.StarterKit, S.UniqueID.configure({ types: ['paragraph', 'heading'], attributeName: 'id',
        generateID: () => Math.random().toString(36).slice(2, 12) }), S.Placeholder.configure({ placeholder: 'Schreiben …' })] });
    return { pm: !!d.querySelector('.ProseMirror'), ws: getComputedStyle(d.querySelector('.ProseMirror')).whiteSpace };
  });
  ok('Editor mountet, Basis-CSS aus saga-pm.css greift', ed.pm && /break-spaces|pre-wrap/.test(ed.ws), ed);
  await p.click('#tt-test .ProseMirror'); await p.keyboard.press('End');
  await p.keyboard.type(' Welt'); await p.keyboard.press('Enter'); await p.keyboard.type('## Titel');
  await p.keyboard.press('Control+b');
  const j = await p.evaluate(() => window.__ed.getJSON());
  const c = j.content || [];
  ok('Tippen landet im Dokument (getJSON)', c[0] && c[0].content[0].text === 'Hallo Welt' && c[1] && c[1].type === 'heading', j);
  ok('UniqueID vergibt IDs', c.length >= 2 && c.every((n) => n.attrs && /^[a-z0-9]{6,}$/.test(n.attrs.id || '')) && c[0].attrs.id !== c[1].attrs.id, c.map((n) => n.attrs));
  ok('Kein <style> eingehaengt (injectCSS:false)', (await p.evaluate(() => document.querySelectorAll('style').length)) === styles0);

  // Live-Bundle: eine yjs-, eine ProseMirror-Instanz
  await p.addScriptTag({ url: '/static/vendor/tiptap/saga-y.js' });
  const live = await p.evaluate(async () => {
    const S = window.SagaPM, L = window.SagaY, Y = L.Y, out = {};
    out.api = ['Y', 'awareness', 'sync', 'ytiptap', 'Collaboration', 'CollaborationCaret'].filter((k) => !L[k]);
    const doc = new Y.Doc(), d = document.createElement('div'); d.id = 'tt-live'; document.body.prepend(d);
    const dh = document.createElement('div'); dh.className = 'tt-dh'; dh.textContent = '::';
    const ed = new S.core.Editor({ element: d, injectCSS: false,
      extensions: [S.StarterKit.configure({ undoRedo: false }), L.Collaboration.configure({ document: doc, field: 'prosemirror' }),
        S.DragHandle.configure({ element: dh })] });
    // ySyncPlugin (Live-Bundle) arbeitet mit dem EditorState aus dem Kern: gleiche Klasse, gleicher PluginKey
    const ys = L.ytiptap.ySyncPluginKey.getState(ed.state);
    out.sync = !!(ys && ys.binding && ys.type === doc.getXmlFragment('prosemirror'));
    out.sameState = ed.state instanceof S.pm.state.EditorState && ed.state.selection instanceof S.pm.state.Selection;
    // Fremdes Y.Doc (wie vom Server) mit Inhalt fuellen und als Update anwenden
    const remote = new Y.Doc();
    L.ytiptap.prosemirrorJSONToYXmlFragment(ed.schema, { type: 'doc', content: [
      { type: 'paragraph', content: [{ type: 'text', text: 'von fern', marks: [{ type: 'bold' }] }] },
      { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: 'Live' }] }] }, remote.getXmlFragment('prosemirror'));
    Y.applyUpdate(doc, Y.encodeStateAsUpdate(remote));
    await new Promise((r) => setTimeout(r, 100));
    out.html = ed.getHTML();
    // lokale Aenderung geht ins Y.Doc zurueck
    ed.commands.insertContentAt(ed.state.doc.content.size, '<p>lokal</p>');
    const back = new Y.Doc(); Y.applyUpdate(back, Y.encodeStateAsUpdate(doc));
    out.back = back.getXmlFragment('prosemirror').toString();
    // DragHandle-Weiterleitung (y-shim) sieht den Live-Zustand
    out.changeOrigin = L.isChangeOrigin(ed.state.tr.setMeta(L.ytiptap.ySyncPluginKey, {}));
    const aw = new L.awareness.Awareness(doc); aw.setLocalStateField('user', { name: 'x' }); out.aw = aw.getStates().size;
    ed.destroy();
    return out;
  });
  ok('SagaY erfuellt den Exportvertrag', !live.api.length, live.api);
  ok('ySyncPlugin haengt am Kern-Editor (eine ProseMirror-Instanz)', live.sync && live.sameState, live);
  ok('Y-Update von fremdem Y.Doc erscheint im Editor', /<strong>von fern<\/strong>/.test(live.html) && /<h2>Live<\/h2>/.test(live.html), live.html);
  ok('Lokale Eingabe landet im Y.Doc', /lokal/.test(live.back) && /von fern/.test(live.back), live.back);
  ok('isChangeOrigin + Awareness laufen', live.changeOrigin === true && live.aw === 1, live);
  const multi = cons.filter((t) => /already imported|multiple instances|duplicate/i.test(t));
  ok('Keine Warnung zu doppelten Instanzen (yjs/ProseMirror)', !multi.length, multi);
  ok('Kein <style> nach Live-Editor', (await p.evaluate(() => document.querySelectorAll('style').length)) === styles0);
  const v = await p.evaluate(() => window.__csp);
  ok('Keine CSP-Verletzung', !v.length, v);
  ok('Keine Seitenfehler', !errs.length, errs);
  await br.close();
  console.log('T1-Bundle ' + res.filter(Boolean).length + '/' + res.length);
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
