// Tiptap-Umbau T4d (Querschnitte, Konzept 3.3): Abnahmekarte fuer Zwischenablage (static/pm-clip.js, pm-tools-medien.js
// clipList, pm-struktur.js copySel), Kommentare am Block (static/comments.js), Inhaltsverzeichnis (static/keys.js),
// KI (static/ai.js ueber die Fassade), Peek (/notes?embed=1) und Bedienhilfen (ARIA, Ansage beim Verschieben).
//  1. Kopieren einer Textauswahl ueber Bloecke (Absatz, Bild, Absatz): HTML mit <img>, Editor.js-JSON, Saga-Format;
//     Einfuegen auf der Seite eines anderen Kontos -> Bild mit neuem Dateiverweis, Speichern klappt. T3-Weg (HTML aus der
//     Blockauswahl ohne JSON) ebenso. UniqueID: Einfuegen auf derselben Seite -> keine doppelten IDs.
//     Klassischer Editor (Konto-Wahl aus) -> neuer Editor und zurueck, je mit Bild auf fremder Seite.
//     Fremdes HTML (Word, Google Docs, Webseite; Fixtures unten): Bloecke nach Positivliste, keine Skripte, keine Bilder
//     geladen (nur Link), in einem Listenpunkt nur Inline.
//  2. Kommentare: Zahl am Block (auch an Bild/Werkzeug/verschachtelt), Klick -> Seitenleiste + Hervorhebung, "+" beim
//     Ueberfahren, Speichern unveraendert (keine Fremdknoten im Inhalt).
//  3. Inhaltsverzeichnis: Ueberschriften + Toggle-Ueberschriften, live aktualisiert, Klick scrollt.
//  4. KI (Server-KI per page.route gespielt): markierte Bloecke gehen an den Server, Ersetzen/Darunter einfuegen.
//  5. Peek: Datenbank-Eintrag im Rahmen, Tippen, Esc schliesst und speichert; Oeffnen/Schliessen ohne Speicherleck.
//  6. ARIA: Slash-Menue listbox/option, Block-Menue menu, Griff per Tastatur, Ansage beim Verschieben, Fokus zurueck.
// Beide Modi: notes.live aus (Stufe 1, PATCH) und an (Live) - der Test liest den Schalter aus /api/version.
// Nur mit SAGA_EDITOR=tiptap; Server mit Admin editor.engine = choice (der Test stellt die Konto-Wahl selbst um).
// Lokal: HK_TEST_BASE=... CK=<Cookie hk-test-a> CK2=<Cookie hk-test-b> HANDLE2=<Handle von hk-test-b>
//        SAGA_EDITOR=tiptap node tests/browser_tiptap_quer.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2, HANDLE2 = process.env.HANDLE2;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);
const J = (x) => JSON.stringify(x);
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==', 'base64');

// --- Fixtures: Ausschnitte, wie Word, Google Docs und Webseiten sie in die Zwischenablage legen ----------------------
const WORD = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word"><head>
<meta charset="utf-8"><style><!-- p.MsoNormal {margin:0cm;} --></style></head><body lang=DE><!--StartFragment-->
<h1>Word Titel</h1>
<p class=MsoNormal>Erster <b>fett</b> und <i>kursiv</i> und <span style='text-decoration:underline'>unter</span><o:p></o:p></p>
<p class=MsoListParagraphCxSpFirst style='text-indent:-18.0pt;mso-list:l0 level1 lfo1'><![if !supportLists]><span style='font-family:Symbol;mso-list:Ignore'>·<span style='font:7.0pt'>&nbsp;&nbsp;</span></span><![endif]>Punkt A<o:p></o:p></p>
<p class=MsoListParagraphCxSpMiddle style='text-indent:-18.0pt;mso-list:l0 level2 lfo1'><span style='mso-list:Ignore'>o<span>&nbsp;</span></span>Punkt A1<o:p></o:p></p>
<p class=MsoListParagraphCxSpLast style='text-indent:-18.0pt;mso-list:l0 level1 lfo1'><span style='mso-list:Ignore'>·<span>&nbsp;</span></span>Punkt B<o:p></o:p></p>
<table class=MsoTableGrid border=1><tr><td><p class=MsoNormal>K1</p></td><td><p class=MsoNormal>K2</p></td></tr><tr><td><p class=MsoNormal>W1</p></td><td><p class=MsoNormal><b>W2</b></p></td></tr></table>
<!--EndFragment--></body></html>`;
const GDOCS = `<meta charset="utf-8"><b style="font-weight:normal;" id="docs-internal-guid-1234abcd"><h2 dir="ltr"><span style="font-size:16pt;font-weight:400">GDocs Kopf</span></h2>
<p dir="ltr"><span style="font-weight:700">Fett</span><span style="font-weight:400"> normal </span><span style="font-style:italic">schraeg</span> <span style="text-decoration:line-through">durch</span> <a href="https://example.org/x"><span style="color:#1155cc">Link</span></a></p>
<ul><li dir="ltr" aria-level="1"><p dir="ltr" role="presentation"><span>G eins</span></p></li><li dir="ltr" aria-level="2"><p dir="ltr" role="presentation"><span>G zwei tief</span></p></li></ul>
<ol><li aria-level="1"><p><span>Nummer 1</span></p></li></ol>
<div dir="ltr"><table><colgroup><col></colgroup><tbody><tr><td><p><span>T1</span></p></td></tr></tbody></table></div></b>`;
const WEB = `<h5>Kleine Ueberschrift</h5><blockquote><p>Ein Zitat</p></blockquote><pre><code>let x = 1;
x++;</code></pre><hr><p>Bild: <img src="https://bilder.example/a.png" alt="Fremdbild"> und <img src="data:image/png;base64,iVBORw0KGgo=" alt="inline"></p>
<script>window.__xss=1</script><p onclick="window.__xss=2" style="color:red">Text mit <span class="hk-c-red">Klasse</span> <a href="javascript:window.__xss=3">boese</a></p>
<ul><li><input type="checkbox" checked> Erledigt</li><li><input type="checkbox"> Offen</li></ul>`;

(async () => {
  if (process.env.SAGA_EDITOR !== 'tiptap') { console.log('Nur mit SAGA_EDITOR=tiptap'); process.exit(2); }
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ext = [];
  async function session(ck) {
    const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: ck, domain: new URL(BASE).hostname, path: '/' }]);
    await ctx.route((u) => !u.href.startsWith(BASE), (r) => { ext.push(r.request().url()); r.fulfill({ status: 204, body: '' }); });
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
  const ver = await p.api('GET', '/api/version'), LIVE = (ver.features || []).includes('live1');
  console.log('     Modus: notes.live ' + (LIVE ? 'AN (Live)' : 'aus (Stufe 1)'));
  await D.setEditor(p, 'tiptap');
  const made = [];
  const mk = async (title, blocks, pg = p) => { const id = (await pg.api('POST', '/api/notes', { title })).id; made.push([pg, id]); if (blocks) await pg.api('PATCH', '/api/notes/' + id, { body: J({ blocks }), format: 'blocks' }); return id; };
  const get = async (id, pg = p) => JSON.parse((await pg.api('GET', '/api/notes/' + id)).body || '{}').blocks || [];
  async function open(id, pg = p, sel = '#pg-editor .pm-doc') {
    await pg.goto(BASE + '/notes#' + id); await pg.reload();
    for (let i = 0; i < 120; i++) { if (await pg.evaluate(([id, sel]) => window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector(sel), [id, sel])) break; await sleep(50); }
    await sleep(LIVE ? 1500 : 700);
    pg.patches = 0;
  }
  // gespeicherter Stand: Stufe 1 nach dem PATCH, live nach der Materialisierung (COLLAB_MAT_DELAY)
  async function saved(id, pg = p, ms) { await sleep(ms || (LIVE ? 2500 : 1600)); return get(id, pg); }
  const upload = (name, pg = p) => pg.evaluate(async ([b64, name]) => {
    const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)), fd = new FormData();
    fd.append('image', new File([bin], name, { type: 'image/png' }));
    return (await fetch('/api/files', { method: 'POST', headers: { 'X-Saga': '1' }, body: fd })).json();
  }, [PNG.toString('base64'), name]);
  // Zwischenablage im Browser: copy/cut auf dem Editor, paste mit einer DataTransfer (wie Strg+C/Strg+V)
  const copyPm = (pg = p, cut = false) => pg.evaluate((cut) => {
    const dt = new DataTransfer(), e = new ClipboardEvent(cut ? 'cut' : 'copy', { clipboardData: dt, bubbles: true, cancelable: true });
    HKPages.k.editor().editor().view.dom.dispatchEvent(e);
    const o = {}; dt.types.forEach((t) => { o[t] = dt.getData(t); }); return o;
  }, cut);
  const pastePm = (data, pg = p) => pg.evaluate((data) => {
    const dt = new DataTransfer(); Object.keys(data).forEach((k) => dt.setData(k, data[k]));
    HKPages.k.editor().editor().view.dom.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  }, data);
  // Textauswahl von (Block, Versatz) bis (Block, Versatz) ueber ProseMirror-Positionen
  const selText = (a, ao, b, bo, pg = p) => pg.evaluate(([a, ao, b, bo]) => {
    const e = HKPages.k.editor().editor(), doc = e.state.doc; let pa = null, pb = null;
    doc.descendants((n, pos) => { if (n.attrs && n.attrs.id === a) pa = pos; if (n.attrs && n.attrs.id === b) pb = pos; });
    const inner = (pos) => { let q = null; doc.nodeAt(pos).descendants((c, o) => { if (q == null && c.isTextblock) q = pos + 1 + o + 1; return q == null; }); return q != null ? q : pos + 1; };
    e.view.focus();
    e.commands.setTextSelection({ from: inner(pa) + ao, to: inner(pb) + bo });
    return e.state.selection.from < e.state.selection.to;
  }, [a, ao, b, bo]);
  const ids = (l) => { const o = []; (function rec(x) { (x || []).forEach((b) => { if (b.id) o.push(b.id); const d = b.data || {}; rec(d.blocks); (d.cols || []).forEach((c) => rec(c.blocks)); }); })(l); return o; };

  // =================================================================================================================
  // 1) Zwischenablage
  const up1 = await upload('quer-' + RUN + '.png');
  ok('Hochladen: Bild fuer die Proben', up1 && up1.file && /^\/files\/[0-9a-f]{24}$/.test(up1.file.url), up1);
  const S = await mk('TQ Quelle ' + RUN, [
    { id: 'q1', type: 'paragraph', data: { text: 'Vor dem <b>Bild</b>' } },
    { id: 'qi', type: 'image', data: { file: { url: up1.file.url }, caption: 'Unter <i>schrift</i>', withBorder: false, withBackground: false, stretched: false } },
    { id: 'q2', type: 'paragraph', data: { text: 'Nach dem Bild' } },
    { id: 'q3', type: 'paragraph', data: { text: 'Nur innen kopieren' } }]);
  await open(S);
  ok('Quelle: Tiptap-Editor geladen', await p.evaluate(() => HKPages.k.doc.kind === 'tiptap'));
  // 1a) Textbereich ueber ein Bild: wird zur Blockauswahl (T-E5) und kopiert mit allen Formaten
  await selText('q1', 4, 'q2', 4); await sleep(100);
  const c1 = await copyPm();
  const ej = JSON.parse(c1['application/x-editor-js'] || '[]'), sb = JSON.parse(c1['application/x-saga-blocks'] || '{}');
  ok('Kopieren ueber Bloecke: Editor.js-JSON (klassischer Editor) mit Absatz, Bild, Absatz', J(ej.map((b) => b.tool)) === J(['paragraph', 'image', 'paragraph']) && ej[1].data.file.url === up1.file.url, ej);
  ok('... Saga-Format application/x-saga-blocks {v:1, blocks}', sb.v === 1 && J((sb.blocks || []).map((b) => b.type)) === J(['paragraph', 'image', 'paragraph']), sb);
  ok('... HTML mit <img src=absolut> (andere Programme) und Schema-Attributen, Text', /<img [^>]*src="http[^"]*\/files\//.test(c1['text/html'] || '') && /data-block-type="image"/.test(c1['text/html'] || '') && /Bild/.test(c1['text/plain'] || ''), c1['text/html']);
  await p.evaluate(() => HKPages.k.doc.select([]));
  // 1b) Innerhalb eines Absatzes: Standard (nur Text/HTML, kein Block-JSON)
  await selText('q3', 0, 'q3', 4);
  const c2 = await copyPm();
  ok('Kopieren innerhalb eines Absatzes: kein Block-JSON (Text wird beim Einfuegen eingefuegt)', !c2['application/x-editor-js'] && !c2['application/x-saga-blocks'] && /Nur/.test(c2['text/plain'] || ''), Object.keys(c2));
  // 1c) Blockauswahl: copySel mit Saga-Format
  await D.select(p, ['q1', 'qi']);
  const c3 = await copyPm();
  ok('Blockauswahl kopieren: auch Saga-Format, HTML mit <img>', JSON.parse(c3['application/x-saga-blocks'] || '{}').v === 1 && /data-block-type/.test(c3['text/html'] || '') && /<img /.test(c3['text/html'] || ''), Object.keys(c3));
  await p.evaluate(() => HKPages.k.doc.select([]));
  // 1c2) Textauswahl ueber zwei Listenpunkte (bleibt Text, pm-clip.js): Liste mit den Teilen als Block-JSON
  const LS = await mk('TQ Listenauswahl ' + RUN, [{ id: 'ls', type: 'list', data: { style: 'unordered', meta: {}, items: [{ content: 'Apfel rot', meta: {}, items: [] }, { content: 'Birne gelb', meta: {}, items: [] }] } }]);
  await open(LS);
  await p.evaluate(() => { const e = HKPages.k.editor().editor(), t = []; e.state.doc.descendants((n, pos) => { if (n.type.name === 'ltext') t.push(pos + 1); }); e.view.focus(); e.commands.setTextSelection({ from: t[0] + 6, to: t[1] + 5 }); });
  await sleep(100);
  const c6 = await copyPm();
  const s6 = JSON.parse(c6['application/x-saga-blocks'] || '{}');
  ok('Textauswahl ueber zwei Listenpunkte: Saga-Format mit der Teil-Liste ("rot", "Birne")', (await p.evaluate(() => HKPages.k.doc.selected().length)) === 0 && s6.blocks && s6.blocks.length === 1 && s6.blocks[0].type === 'list'
    && J(s6.blocks[0].data.items.map((i) => i.content)) === J(['rot', 'Birne']) && JSON.parse(c6['application/x-editor-js'] || '[]').length === 1, c6);
  // 1d) Einfuegen auf der Seite eines anderen Kontos (Rolle edit): Datei wird kopiert, Speichern klappt
  let PB = null, B = null, pb = null;
  if (CK2 && HANDLE2) {
    B = await session(CK2); pb = B.p;
    await D.setEditor(pb, 'tiptap');
    PB = (await pb.api('POST', '/api/notes', { title: 'TQ Ziel B ' + RUN })).id; made.push([pb, PB]);
    await pb.api('PATCH', '/api/notes/' + PB, { body: J({ blocks: [{ id: 'zb', type: 'paragraph', data: { text: 'Ziel' } }, { id: 'zc', type: 'paragraph', data: { text: '' } }] }), format: 'blocks' });
    await pb.api('PUT', '/api/notes/' + PB + '/shares', { type: 'user', target: process.env.HANDLE1 || 'hk-test-a', role: 'edit' });
    await p.api('PUT', '/api/notes/' + S + '/shares', { type: 'user', target: HANDLE2, role: 'view' });
    await open(PB);
    await D.focus(p, 'zc', 'end');
    await pastePm(c1);
    const b1 = await saved(PB, pb);
    const im = b1.find((x) => x.type === 'image');
    ok('Einfuegen Textbereich mit Bild auf fremder Seite: Bild mit neuem Dateiverweis, Text dabei, gespeichert',
      im && /^\/files\/[0-9a-f]{24}$/.test(im.data.file.url) && im.data.file.url !== up1.file.url && /schrift/.test(im.data.caption) && b1.some((x) => /Bild/.test(x.data.text || '')) && b1.some((x) => /Nach/.test(x.data.text || '')), b1);
    ok('... kein "Nicht gespeichert" (Seite ohne offene Aenderung)', await p.evaluate(() => !document.querySelector('.pg-save.err, .save-err, [data-save="error"]')) && !(await p.evaluate(() => HKPages.k.doc && HKPages.k.editor().pending && HKPages.k.editor().pending())), null);
    // 1e) T3-Weg: nur HTML aus der Blockauswahl (ohne JSON) - Verweise werden trotzdem umgeschrieben
    await D.focus(p, 'zb', 'end');
    await pastePm({ 'text/html': c3['text/html'], 'text/plain': c3['text/plain'] || '' });
    const b2 = await saved(PB, pb);
    const ims = b2.filter((x) => x.type === 'image');
    ok('Einfuegen nur HTML (T3-Weg pasteBlocks) mit fremdem Bild: neuer Dateiverweis, gespeichert', ims.length === 2 && ims.every((x) => x.data.file.url !== up1.file.url && /^\/files\//.test(x.data.file.url)), b2);
    ok('... IDs eindeutig', new Set(ids(b2)).size === ids(b2).length, ids(b2));
  }
  // 1f) UniqueID: HTML-Kopie (data-pm-slice, mit IDs) auf derselben Seite einfuegen -> keine doppelten IDs
  await open(S);
  await selText('q2', 0, 'q3', 3);
  const c4 = await copyPm();
  await D.focus(p, 'q3', 'end');
  await pastePm({ 'text/html': c4['text/html'], 'text/plain': c4['text/plain'] });
  const b3 = await saved(S);
  ok('Einfuegen auf derselben Seite: neue Bloecke, keine doppelten IDs (UniqueID)', b3.length > 4 && new Set(ids(b3)).size === ids(b3).length, ids(b3));
  // 1g) Fremdes HTML (je Fixture eine Seite, Schreibmarke im leeren Absatz)
  async function pasteForeign(name, html) {
    const id = await mk('TQ ' + name + ' ' + RUN, [{ id: 'f0', type: 'paragraph', data: { text: 'Start' } }, { id: 'f1', type: 'paragraph', data: { text: '' } }]);
    await open(id);
    await p.evaluate(() => { window.__xss = 0; });
    await D.focus(p, 'f1', 'end'); await pastePm({ 'text/html': html, 'text/plain': name });
    return saved(id);
  }
  await p.evaluate(() => { window.__xss = 0; });
  const ext0 = ext.length;
  let bf = await pasteForeign('Word', WORD);
  const tys = bf.map((b) => b.type);
  const wl = bf.find((b) => b.type === 'list'), wt = bf.find((b) => b.type === 'table');
  ok('Word: Ueberschrift, Absatz mit fett/kursiv/unterstrichen, Liste (verschachtelt aus mso-list), Tabelle',
    tys.includes('header') && bf.some((b) => b.type === 'paragraph' && /<b>fett<\/b>/.test(b.data.text) && /<i>kursiv<\/i>/.test(b.data.text) && /<u>unter<\/u>/.test(b.data.text))
    && wl && wl.data.items.length === 2 && wl.data.items[0].content === 'Punkt A' && wl.data.items[0].items[0].content === 'Punkt A1' && !/·/.test(J(wl))
    && wt && J(wt.data.content) === J([['K1', 'K2'], ['W1', '<b>W2</b>']]), bf);
  ok('... keine Word-Reste (o:p, mso, style, Kommentare)', !/mso|o:p|style=|StartFragment|&lt;!/.test(J(bf)), J(bf).slice(0, 400));
  bf = await pasteForeign('GDocs', GDOCS);
  const gh = bf.find((b) => b.type === 'header' && /GDocs/.test(b.data.text)), gp = bf.find((b) => b.type === 'paragraph' && /schraeg/.test(b.data.text || ''));
  const gl = bf.filter((b) => b.type === 'list');
  ok('Google Docs: <b font-weight:normal>-Huelle nicht fett, Ueberschrift h2, Stile -> b/i/s, Link',
    gh && gh.data.level === 2 && !/<b>/.test(gh.data.text) && gp && /<b>Fett<\/b>/.test(gp.data.text) && /<i>schraeg<\/i>/.test(gp.data.text) && /<s>durch<\/s>/.test(gp.data.text) && /href="https:\/\/example.org\/x"/.test(gp.data.text) && !/color/.test(gp.data.text), bf);
  ok('... Liste mit aria-level verschachtelt, nummerierte Liste, Tabelle', gl.some((l) => l.data.style === 'unordered' && l.data.items[0].content === 'G eins' && l.data.items[0].items[0].content === 'G zwei tief')
    && gl.some((l) => l.data.style === 'ordered') && bf.some((b) => b.type === 'table' && b.data.content[0][0] === 'T1'), bf);
  bf = await pasteForeign('Web', WEB);
  const wq = bf.find((b) => b.type === 'quote'), wc = bf.find((b) => b.type === 'code'), wh = bf.find((b) => b.type === 'header' && /Kleine/.test(b.data.text));
  const wi = bf.find((b) => /Fremdbild/.test(b.data.text || '')), wcl = bf.find((b) => b.type === 'list' && b.data.style === 'checklist');
  ok('Webseite: h5 -> Ueberschrift 4, Zitat, Code (Zeilen), Trennlinie, Checkliste', wh && wh.data.level === 4 && wq && /Ein Zitat/.test(wq.data.text) && wc && wc.data.code === 'let x = 1;\nx++;'
    && bf.some((b) => b.type === 'delimiter') && wcl && wcl.data.items.length === 2 && wcl.data.items[0].meta.checked === true && wcl.data.items[1].meta.checked === false, bf);
  ok('... Bilder nicht geladen: fremdes Bild als Link, data:-Bild weg; kein Bild-Block', wi && /<a href="https:\/\/bilder.example\/a.png"[^>]*>Fremdbild<\/a>/.test(wi.data.text) && !/data:image/.test(J(bf)) && !bf.some((b) => b.type === 'image')
    && !ext.slice(ext0).some((u) => /bilder\.example|data:/.test(u)), [wi, ext.slice(ext0)]);
  ok('... kein Skript, kein onclick/javascript:, fremde Klassen/Stile weg', !(await p.evaluate(() => window.__xss)) && !/onclick|javascript:|hk-c-red|style=/.test(J(bf))
    && (await p.evaluate(() => document.querySelectorAll('#pg-editor [onclick], #pg-editor script, #pg-editor a[href^="javascript"]').length)) === 0, J(bf).slice(0, 300));
  // ein Satz von einer Webseite mitten in einen Absatz: wird eingefuegt, kein neuer Block
  const I1 = await mk('TQ Inline ' + RUN, [{ id: 'i1', type: 'paragraph', data: { text: 'AnfangEnde' } }]);
  await open(I1);
  await p.evaluate(() => { const e = HKPages.k.editor().editor(); e.view.focus(); e.commands.setTextSelection(1 + 6); });
  await pastePm({ 'text/html': '<span style="font-weight:bold">[fett]</span><span> normal</span>', 'text/plain': '[fett] normal' });
  const bi = await saved(I1);
  ok('Fremder Satz mitten im Absatz: eingefuegt (fett), kein neuer Block', bi.length === 1 && bi[0].data.text === 'Anfang<b>[fett]</b> normalEnde', bi);
  // in einem Listenpunkt: nur Inline
  const L = await mk('TQ Liste ' + RUN, [{ id: 'l1', type: 'list', data: { style: 'unordered', meta: {}, items: [{ content: 'Punkt', meta: {}, items: [] }] } }]);
  await open(L);
  await p.evaluate(() => { const e = HKPages.k.editor().editor(); let q = null; e.state.doc.descendants((n, pos) => { if (q == null && n.type.name === 'ltext') q = pos + 1 + n.content.size; }); e.view.focus(); e.commands.setTextSelection(q); });
  await pastePm({ 'text/html': '<h1>Gross</h1><p>und <b>fett</b></p>', 'text/plain': 'x' });
  const bl = await saved(L);
  ok('Fremdes HTML in einem Listenpunkt: nur Inline (kein neuer Block, Absaetze mit <br>)', bl.length === 1 && bl[0].type === 'list' && bl[0].data.items.length === 1 && /PunktGross<br>und <b>fett<\/b>/.test(bl[0].data.items[0].content), bl);

  // 1h) Klassischer Editor <-> neuer Editor (Konto-Wahl aus/an)
  if (PB) {
    await D.setEditor(p, 'editorjs');
    const C = await mk('TQ Klassisch ' + RUN, [
      { id: 'k1', type: 'paragraph', data: { text: 'Alt <b>eins</b>' } },
      { id: 'k2', type: 'list', data: { style: 'ordered', meta: {}, items: [{ content: 'Nr 1', meta: {}, items: [{ content: 'Nr 1.1', meta: {}, items: [] }] }] } },
      { id: 'k3', type: 'image', data: { file: { url: up1.file.url }, caption: 'Alt-Bild', withBorder: true, withBackground: false, stretched: false } },
      { id: 'k4', type: 'toggle', data: { title: 'Auf', open: true, blocks: [{ id: 'k5', type: 'paragraph', data: { text: 'Innen' } }] } }]);
    await p.api('PUT', '/api/notes/' + C + '/shares', { type: 'user', target: HANDLE2, role: 'view' });
    await open(C, p, '#pg-editor .codex-editor');
    ok('Konto-Wahl aus: klassischer Editor', await p.evaluate(() => HKPages.k.doc.kind === 'editorjs'));
    await p.click('#pg-editor [data-block-id="k1"] [contenteditable="true"]');
    await D.select(p, ['k1', 'k2', 'k3', 'k4']); await sleep(300);
    // echtes Strg+C (Editor.js kopiert markierte Bloecke selbst); Inhalt nach Editor.js abgreifen
    await p.evaluate(() => { window.__clip = null; window.addEventListener('copy', (e) => { const o = {}; e.clipboardData.types.forEach((t) => { o[t] = e.clipboardData.getData(t); }); window.__clip = o; }); });
    await p.keyboard.press((process.platform === 'darwin' ? 'Meta' : 'Control') + '+c'); await sleep(600);
    const ce = await p.evaluate(() => window.__clip || {});
    await sleep(500);
    const cej = JSON.parse(ce['application/x-editor-js'] || '[]');
    ok('Klassisch kopieren (alle Bloecke): Editor.js-JSON', cej.length === 4 && cej[2].tool === 'image', cej.map((b) => b.tool));
    await D.setEditor(p, 'tiptap');
    await open(PB);
    const before = (await get(PB, pb)).length;
    await D.focus(p, 'zb', 'end');
    await pastePm(ce);
    const b4 = await saved(PB, pb);
    const add = b4.slice(1, 1 + 4);
    ok('Klassisch -> neuer Editor: Absatz, Liste (verschachtelt), Bild (neuer Verweis), Toggle als Bloecke; neue IDs',
      b4.length === before + 4 && J(add.map((b) => b.type)) === J(['paragraph', 'list', 'image', 'toggle']) && /<b>eins<\/b>/.test(add[0].data.text)
      && add[1].data.items[0].items[0].content === 'Nr 1.1' && add[2].data.file.url !== up1.file.url && add[2].data.withBorder === true
      && add[3].data.blocks[0].data.text === 'Innen' && !add.some((b) => ['k1', 'k2', 'k3', 'k4'].includes(b.id)), b4);
    // neuer -> klassischer Editor (Blockauswahl im neuen Editor, Einfuegen im klassischen auf der fremden Seite)
    await open(S);
    await D.select(p, ['q1', 'qi']);
    const c5 = await copyPm();
    await p.evaluate(() => HKPages.k.doc.select([]));
    await D.setEditor(p, 'editorjs');
    const PB2 = (await pb.api('POST', '/api/notes', { title: 'TQ Ziel B2 ' + RUN })).id; made.push([pb, PB2]);
    await pb.api('PATCH', '/api/notes/' + PB2, { body: J({ blocks: [{ id: 'y1', type: 'paragraph', data: { text: 'Ziel' } }, { id: 'y2', type: 'paragraph', data: { text: 'Ende' } }] }), format: 'blocks' });
    await pb.api('PUT', '/api/notes/' + PB2 + '/shares', { type: 'user', target: process.env.HANDLE1 || 'hk-test-a', role: 'edit' });
    await open(PB2, p, '#pg-editor .codex-editor');
    await p.click('#pg-editor [data-block-id="y1"] [contenteditable="true"]');
    await p.evaluate((data) => {
      const dt = new DataTransfer(); Object.keys(data).forEach((k) => dt.setData(k, data[k]));
      document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    }, c5);
    await sleep(2500);
    const b5 = await get(PB2, pb);
    const im5 = b5.find((b) => b.type === 'image');
    ok('Neuer -> klassischer Editor: Absatz + Bild (neuer Verweis), gespeichert', b5.some((b) => /Vor dem <b>Bild<\/b>/.test(b.data.text || '')) && im5 && im5.data.file.url !== up1.file.url && /^\/files\//.test(im5.data.file.url), b5);
    await D.setEditor(p, 'tiptap');
  }
  ok('Abschnitt 1: keine Seitenfehler', !p.errs.length && !(pb && pb.errs.length), [p.errs, pb && pb.errs]);

  // =================================================================================================================
  // Ende
  ok('CSP: keine Verletzungen', (await p.evaluate(() => window.__csp.length)) === 0, await p.evaluate(() => window.__csp));
  for (const [pg, id] of made.reverse()) await pg.api('DELETE', '/api/notes/' + id).catch(() => {});
  await D.setEditor(p, was.tiptap ? 'tiptap' : 'editorjs');
  if (pb) await D.setEditor(pb, was.tiptap ? 'tiptap' : 'editorjs');
  await br.close();
  console.log(res.filter(Boolean).length + '/' + res.length, res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER');
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
