// Tiptap-Umbau T4a (Medien, static/pm-tools-medien.js): Abnahmekarten fuer Bild (Knoten image(icap) mit Unterschrift,
// Hochladen, Breite, Ausrichtung, Rahmen/Hintergrund/volle Breite), Datei (eigene Node-View), Bild aus Immich (Slash nur
// fuer den Eigentuemer), Nextcloud-Datei (Bruecke, Signatur sig unveraendert, fremde Bloecke abweisen) und Einbetten
// (YouTube/Link-Karte, URL einfuegen). Je Werkzeug: anlegen ueber "/", bearbeiten, Rolle Lesen, speichern -> GET,
// Undo/Redo, Kopieren eines Bildes auf eine andere Seite (Dateiverweis ueber /api/files/copy), unveraenderte
// Medienbloecke byte-gleich, beim Laden kein PATCH, CSP = 0. Keine externen Netzaufrufe: YouTube/Nextcloud werden
// abgefangen (page.route), Bilder sind kleine PNGs aus dem Test.
// Nur mit SAGA_EDITOR=tiptap; der Server braucht Admin editor.engine = choice (der Test stellt die Konto-Wahl selbst um).
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> CK2=<Cookie hk-test-b> HANDLE2=<Handle von hk-test-b>
//        SAGA_EDITOR=tiptap node tests/browser_tiptap_medien.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2, HANDLE2 = process.env.HANDLE2;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);
const MOD = process.platform === 'darwin' ? 'Meta' : 'Control';
const J = (x) => JSON.stringify(x);
// 1x1-PNG (rot) - kleines gueltiges Bild fuer /api/files
// Signatur eines Nextcloud-Blocks wie saga/nextcloud.py sign() (Test-Secret aus der Umgebung des Testservers)
const crypto = require('crypto');
function ncSign(nid, d) {
  const key = crypto.createHash('sha256').update('hk-nextcloud-block|' + (process.env.SECRET_KEY || process.env.SESSION_SECRET || '')).digest();
  const msg = [nid, d.author || '', d.inst || '', d.fileid || '', d.public === true ? 1 : 0, d.fed === true ? 1 : 0].join('|');
  return crypto.createHmac('sha256', key).update(msg).digest('hex').slice(0, 32);
}
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==', 'base64');

(async () => {
  if (process.env.SAGA_EDITOR !== 'tiptap') { console.log('Nur mit SAGA_EDITOR=tiptap'); process.exit(2); }
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ext = [];
  async function session(ck) {
    const ctx = await br.newContext({ viewport: { width: 1200, height: 900 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: ck, domain: new URL(BASE).hostname, path: '/' }]);
    // keine externen Netzaufrufe (YouTube-Player, fremde Seiten)
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
  await D.setEditor(p, 'tiptap');
  const made = [];
  const mk = async (title, blocks, pg = p) => { const id = (await pg.api('POST', '/api/notes', { title })).id; made.push(id); if (blocks) await pg.api('PATCH', '/api/notes/' + id, { body: J({ blocks }), format: 'blocks' }); return id; };
  const get = async (id) => JSON.parse((await p.api('GET', '/api/notes/' + id)).body || '{}').blocks || [];
  async function open(id, pg = p) {
    await pg.goto(BASE + '/notes#' + id); await pg.reload();
    for (let i = 0; i < 100; i++) { if (await pg.evaluate((id) => window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector('#pg-editor .pm-doc'), id)) break; await sleep(50); }
    await sleep(700);
    pg.patches = 0;
  }
  async function saved(id, ms = 1500) { await sleep(ms); return get(id); }
  const find = (l, id) => l.find((b) => b.id === id);
  const upload = (name) => p.evaluate(async ([b64, name]) => {
    const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)), fd = new FormData();
    fd.append('image', new File([bin], name, { type: 'image/png' }));
    return (await fetch('/api/files', { method: 'POST', headers: { 'X-Saga': '1' }, body: fd })).json();
  }, [PNG.toString('base64'), name]);
  async function slash(q) { await p.keyboard.type('/' + q, { delay: 10 }); await sleep(300); }
  async function menuOf(id) { await p.evaluate((id) => HKPages.k.doc.menu(id), id); await sleep(200); }
  async function menuClick(t) { await p.click('.menu:not([hidden]) .menu-item:has(.mi-t:text-is("' + t + '"))'); await sleep(250); }
  async function subClick(parent, t) {
    await p.hover('.menu:not([hidden]) .menu-item:has(.mi-t:text-is("' + parent + '"))'); await sleep(200);
    await p.click('.menu-sub:not([hidden]) .menu-item:has(.mi-t:text-is("' + t + '"))'); await sleep(250);
  }

  // =================================================================================================================
  // 1) Laden: Medienbloecke als Werkzeuge (keine Karte), byte-gleich, kein PATCH
  const up1 = await upload('eins.png'), up2 = await upload('zwei.png');
  ok('Hochladen eines PNG ueber /api/files', up1.success === 1 && /^\/files\/[0-9a-f]{24}$/.test(up1.file.url), up1);
  const MEDIA = [
    { id: 'im1', type: 'image', data: { file: { url: up1.file.url, name: 'eins.png', zz: 1 }, caption: 'Ein <b>Bild</b> hier', withBorder: true, withBackground: false, stretched: false, width: 60, align: 'left', fremd: 'bleibt' } },
    { id: 'at1', type: 'attaches', data: { file: { url: up2.file.url, name: 'zwei.png', size: 68, extension: 'png' }, title: 'Anhang', extra1: 7 } },
    { id: 'em1', type: 'embed', data: { url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', kind: 'youtube', width: 50, align: 'right', fremd: 2 } },
    { id: 'em2', type: 'embed', data: { url: 'https://example.org/artikel', title: 'Ein Artikel' } },
    { id: 'nc1', type: 'nextcloud', data: { inst: 'https://cloud.example.org', fileid: '42', kind: 'file', name: 'bericht.pdf', mime: 'application/pdf', size: 1000, author: 'hk-test-a', public: false, fed: false } },
    { id: 'pz', type: 'paragraph', data: { text: 'Ende' } }
  ];
  const P1 = await mk('TM Laden ' + RUN);
  const SIG = ncSign(P1, MEDIA[4].data); MEDIA[4].data.sig = SIG;
  await p.api('PATCH', '/api/notes/' + P1, { body: J({ blocks: MEDIA }), format: 'blocks' });
  const orig = await get(P1);
  ok('Server: Medienbloecke gespeichert (Nextcloud-Block bleibt mit Signatur)', orig.length === 6 && find(orig, 'nc1') && find(orig, 'nc1').data.sig === SIG, orig.map((b) => b.type));
  await open(P1);
  ok('Editor ist Tiptap', await D.isEditor(p) === 'tiptap');
  const view = await p.evaluate(() => ({
    cards: [...document.querySelectorAll('#pg-editor .pm-card')].map((c) => c.getAttribute('data-block-type')),
    img: !!document.querySelector('#pg-editor [data-block-id="im1"] img.image-tool__image-picture'),
    cap: (document.querySelector('#pg-editor [data-block-id="im1"] [data-pm-part="icap"]') || {}).innerHTML,
    sz: (() => { const b = document.querySelector('#pg-editor [data-block-id="im1"] .pm-img'); return b && [b.classList.contains('hk-al-left'), b.style.getPropertyValue('--iw'), b.classList.contains('image-tool--withBorder')]; })(),
    att: (document.querySelector('#pg-editor .hk-tool-attaches .pm-att-in') || {}).value,
    yt: (document.querySelector('#pg-editor [data-block-id="em1"] iframe') || {}).src,
    card: (document.querySelector('#pg-editor [data-block-id="em2"] .hk-embed-t') || {}).textContent,
    nc: !!document.querySelector('#pg-editor .hk-tool-nextcloud .nc-cardb')
  }));
  ok('Keine Karte fuer image/attaches/embed/nextcloud', !view.cards.some((t) => ['image', 'attaches', 'embed', 'nextcloud'].includes(t)), view.cards);
  ok('Bild: Bild, Rich-Text-Unterschrift, Ausrichtung/Breite/Rahmen dargestellt', view.img && /<b>Bild<\/b>|<strong>Bild<\/strong>/.test(view.cap || '') && J(view.sz) === J([true, '60%', true]), view);
  ok('Datei: Titel im Feld', view.att === 'Anhang', view.att);
  ok('Einbetten: YouTube ueber youtube-nocookie, Link als Karte', /^https:\/\/www\.youtube-nocookie\.com\/embed\/dQw4w9WgXcQ/.test(view.yt || '') && view.card === 'Ein Artikel', view);
  ok('Nextcloud: Karte der Bruecke (ohne Server: nur Darstellung)', view.nc);
  const same0 = await p.evaluate(() => HKPages.k.editor().save().then((o) => JSON.stringify(o.blocks)));
  ok('Ohne Bearbeitung: save() byte-gleich mit GET (auch sig, fremde Felder)', same0 === J(orig), [same0.slice(0, 300)]);
  await sleep(1500);
  ok('Beim Laden kein PATCH', p.patches === 0, p.patches);

  // 2) Bild bearbeiten: Unterschrift mit Fett, Ausrichtung, Rahmen, Breite (Griff), Undo/Redo
  await p.click('#pg-editor [data-block-id="im1"] [data-pm-part="icap"]');
  await p.keyboard.press('End'); await p.keyboard.type(' und ', { delay: 10 });
  await p.keyboard.press(MOD + '+b'); await p.keyboard.type('fett', { delay: 10 }); await p.keyboard.press(MOD + '+b');
  let b = await saved(P1);
  ok('Unterschrift: Tippen mit Fett gespeichert', find(b, 'im1').data.caption === 'Ein <b>Bild</b> hier und <b>fett</b>', find(b, 'im1').data.caption);
  const rest = (l) => J(l.filter((x) => x.id !== 'im1'));
  ok('Andere Medienbloecke dabei byte-gleich', rest(b) === rest(orig), rest(b).slice(0, 300));
  ok('Bild: fremde Felder (fremd, file.zz) bleiben', find(b, 'im1').data.fremd === 'bleibt' && find(b, 'im1').data.file.zz === 1, find(b, 'im1').data);
  await menuOf('im1');
  const mi = await p.evaluate(() => [...document.querySelectorAll('.menu:not([hidden]) .menu-item .mi-t')].map((x) => x.textContent));
  ok('Block-Menue Bild: Ausrichtung, Rahmen, Hintergrund, volle Breite, Breite zuruecksetzen', ['Ausrichtung', 'Mit Rahmen', 'Mit Hintergrund', 'Volle Breite', 'Breite zurücksetzen'].every((x) => mi.includes(x)) && mi.includes('Bild') === false, mi);
  await subClick('Ausrichtung', 'Zentriert');
  await menuOf('im1'); await menuClick('Mit Rahmen');
  await menuOf('im1'); await menuClick('Volle Breite');
  b = await saved(P1);
  let im = find(b, 'im1').data;
  ok('Ausrichtung/Rahmen/volle Breite je ueber das Block-Menue', im.align === 'center' && im.withBorder === false && im.stretched === true, im);
  // Breite ueber den rechten Griff ziehen (zentriert: doppelter Abstand zur Mitte)
  const g = await p.evaluate(() => { const box = document.querySelector('[data-block-id="im1"] .pm-img').getBoundingClientRect(), h = document.querySelector('[data-block-id="im1"] .hk-rs-r').getBoundingClientRect();
    return { cx: box.left + box.width / 2, w: box.width, hx: h.left + h.width / 2, hy: h.top + h.height / 2 }; });
  await p.mouse.move(g.hx, g.hy); await sleep(150); await p.mouse.down();
  await p.mouse.move(g.cx + g.w * 0.2, g.hy, { steps: 6 }); await p.mouse.up();
  b = await saved(P1);
  im = find(b, 'im1').data;
  ok('Breite per Griff: ein Zug, Wert 10-100 (etwa 40 %)', typeof im.width === 'number' && im.width >= 35 && im.width <= 45, im.width);
  await D.focus(p, 'pz', 'end');
  await p.evaluate(() => HKPages.k.doc.undo()); b = await saved(P1, 1200);
  ok('Undo nimmt die Breite in einem Schritt zurueck', find(b, 'im1').data.width === 60, find(b, 'im1').data.width);
  await p.evaluate(() => HKPages.k.editor().editor().commands.redo()); b = await saved(P1, 1200);
  ok('Redo stellt sie wieder her', find(b, 'im1').data.width === im.width, find(b, 'im1').data.width);
  ok('Nach dem Bearbeiten: Nextcloud weiter byte-gleich (sig)', J(find(b, 'nc1')) === J(find(orig, 'nc1')), find(b, 'nc1'));

  // 3) Einbetten: Breite/Ausrichtung im Block-Menue (nur YouTube), Datei-Titel bearbeiten
  await menuOf('em1');
  await subClick('Ausrichtung', 'Linksbündig');
  await menuOf('em2');
  const mi2 = await p.evaluate(() => [...document.querySelectorAll('.menu:not([hidden]) .menu-item .mi-t')].map((x) => x.textContent));
  await p.keyboard.press('Escape');
  await p.fill('#pg-editor .hk-tool-attaches .pm-att-in', 'Neuer Titel'); await p.press('#pg-editor .hk-tool-attaches .pm-att-in', 'Enter');
  b = await saved(P1);
  ok('Einbetten: Ausrichtung per Block-Menue, fremde Felder bleiben', find(b, 'em1').data.align === 'left' && find(b, 'em1').data.width === 50 && find(b, 'em1').data.fremd === 2, find(b, 'em1').data);
  ok('Einbetten Link-Karte: keine Ausrichtung im Menue', !mi2.includes('Ausrichtung'), mi2);
  ok('Datei: Titel bearbeitet, Datei und fremde Felder bleiben', find(b, 'at1').data.title === 'Neuer Titel' && find(b, 'at1').data.extra1 === 7 && find(b, 'at1').data.file.url === up2.file.url, find(b, 'at1').data);

  // =================================================================================================================
  // 4) Anlegen ueber "/": Slash-Gruppe Medien; Bild (Hochladen), Datei (Hochladen), Einbetten (URL), leere nicht gespeichert
  const P2 = await mk('TM Slash ' + RUN, [{ id: 's0', type: 'paragraph', data: { text: 'Start' } }, { id: 's1', type: 'paragraph', data: { text: '' } }]);
  await open(P2);
  await D.focus(p, 's1', 'end'); await slash('');
  const grp = await p.evaluate(() => { const out = {}; let g = ''; for (const x of document.querySelectorAll('.pm-pop:not([hidden]) > *')) { if (x.classList.contains('hk-at-g')) g = x.textContent; else (out[g] = out[g] || []).push(x.querySelector('.hk-at-t').textContent + (x.querySelector('.pm-mi-d') ? '+d' : '') + (x.querySelector('.hk-emo-e').innerHTML ? '+i' : '')); } return out; });
  ok('Slash: Gruppe Medien mit Bild, Datei, Immich, Nextcloud, Einbetten (Beschreibung + Symbol)', J(grp['Medien']) === J(['Bild+d+i', 'Datei+d+i', 'Bild aus Immich+d+i', 'Nextcloud-Datei+d+i', 'Einbetten (YouTube, Link)+d+i']), grp['Medien']);
  ok('Slash: keine doppelten Medien-Eintraege aus HKTools in "Saga"', !(grp['Saga'] || []).some((x) => /Datei|Einbetten|Nextcloud/.test(x)), grp['Saga']);
  await p.keyboard.press('Escape'); await p.keyboard.press('Backspace');
  await slash('Bild'); await p.keyboard.press('Enter'); await sleep(300);
  const imgNew = await p.evaluate(() => { const b = HKPages.k.doc.blocks().find((x) => x.type === 'image'); return b && { id: b.id, pick: !!b.el.querySelector('.pm-img-pick') }; });
  ok('"/Bild" legt einen leeren Bild-Block mit "Bild auswählen …" an', imgNew && imgNew.pick, imgNew);
  const [fc] = await Promise.all([p.waitForEvent('filechooser'), p.click('[data-block-id="' + imgNew.id + '"] .pm-img-pick')]);
  await fc.setFiles({ name: 'neu.png', mimeType: 'image/png', buffer: PNG });
  for (let i = 0; i < 40 && !(await p.$('[data-block-id="' + imgNew.id + '"] img[src^="/files/"]')); i++) await sleep(100);
  const para = await p.$('#pg-editor .pm-b-paragraph');
  if (para) { await para.click(); await sleep(150); }
  const ph0 = await p.evaluate(() => document.querySelectorAll('#pg-editor [data-pm-part="icap"][data-ph]').length);
  ok('Platzhalter Bildunterschrift fehlt, solange kein Bild/keine Unterschrift fokussiert ist', !!para && ph0 === 0, { para: !!para, ph0 });
  await p.click('[data-block-id="' + imgNew.id + '"] [data-pm-part="icap"]'); await sleep(150);
  // Trilium-Fund #15: Platzhalter "Bildunterschrift" nur an der fokussierten/ausgewaehlten Unterschrift, nicht an allen Bildern
  const ph = await p.evaluate((id) => [...document.querySelectorAll('#pg-editor [data-pm-part="icap"][data-ph]')].map((x) => x.closest('[data-block-id]').getAttribute('data-block-id') === id), imgNew.id);
  ok('Platzhalter Bildunterschrift nur an der fokussierten Unterschrift', ph.length === 1 && ph[0] === true, ph);
  await p.keyboard.type('Neu', { delay: 10 });
  b = await saved(P2);
  const ni = b.find((x) => x.type === 'image');
  ok('Bild hochgeladen + Unterschrift: Block-JSON wie Editor.js (file.url, caption, withBorder ...)', ni && /^\/files\/[0-9a-f]{24}$/.test(ni.data.file.url) && ni.data.caption === 'Neu' && ni.data.withBorder === false && ni.data.stretched === false, ni);
  // Datei ueber "/" mit Hochladen
  await D.focus(p, 's0', 'end'); await p.keyboard.type(' '); await slash('Datei'); await p.keyboard.press('Enter'); await sleep(300);
  const [fc2] = await Promise.all([p.waitForEvent('filechooser'), p.click('#pg-editor .hk-tool-attaches .pm-att-pick')]);
  await fc2.setFiles({ name: 'notiz.txt', mimeType: 'text/plain', buffer: Buffer.from('Hallo Datei') });
  b = await saved(P2, 1800);
  const na = b.find((x) => x.type === 'attaches');
  ok('Datei hochgeladen: {file:{url,name,size,extension}, title}', na && /^\/files\//.test(na.data.file.url) && na.data.file.name === 'notiz.txt' && na.data.title === 'notiz.txt' && na.data.file.size === 11, na);
  // leerer Einbetten-Block wird nicht gespeichert; Link eingeben -> gespeichert
  await D.focus(p, 's0', 'end'); await p.keyboard.type(' ');
  await slash('Einbetten'); await p.keyboard.press('Enter'); await sleep(400);
  b = await saved(P2, 1500);
  ok('Leerer Einbetten-Block wird nicht gespeichert (validate)', !b.some((x) => x.type === 'embed'), b.map((x) => x.type));
  await p.fill('#pg-editor .hk-embed-in', 'https://example.org/seite'); await p.press('#pg-editor .hk-embed-in', 'Enter');
  b = await saved(P2);
  ok('Einbetten: Link eingegeben -> gespeichert', b.some((x) => x.type === 'embed' && x.data.url === 'https://example.org/seite'), b.filter((x) => x.type === 'embed'));
  // URL in leeren Absatz einfuegen -> Einbetten
  await p.evaluate(() => { const l = HKPages.k.doc.blocks(); HKPages.k.doc.insertAfter(l[l.length - 1].id, [{ type: 'paragraph', data: { text: '' } }]); });
  await sleep(100);
  await p.evaluate(() => { const l = HKPages.k.doc.blocks(); HKPages.k.doc.focus(l[l.length - 1].id, 'end'); });
  await p.evaluate(() => { const dt = new DataTransfer(); dt.setData('text/plain', 'https://youtu.be/dQw4w9WgXcQ'); HKPages.k.editor().editor().view.dom.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); });
  b = await saved(P2);
  const yt = b.filter((x) => x.type === 'embed' && /youtu\.be/.test(x.data.url));
  ok('URL einfuegen (leerer Absatz) -> Einbetten, YouTube erkannt (kind youtube)', yt.length === 1 && yt[0].data.kind === 'youtube', b.map((x) => x.type + ':' + (x.data.url || '')));
  // Bild-Datei einfuegen -> Hochladen + Bild-Block
  const nImg = b.filter((x) => x.type === 'image').length;
  await p.evaluate(async (b64) => {
    const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)), dt = new DataTransfer();
    dt.items.add(new File([bin], 'eingefuegt.png', { type: 'image/png' }));
    HKPages.k.editor().editor().view.dom.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  }, PNG.toString('base64'));
  b = await saved(P2, 2000);
  ok('Bild-Datei einfuegen -> hochgeladen, neuer Bild-Block', b.filter((x) => x.type === 'image').length === nImg + 1, b.map((x) => x.type));

  // =================================================================================================================
  // 5) Kopieren eines Bildblocks (Cmd+C auf dem ausgewaehlten Bild) auf eine Seite eines anderen Kontos (Rolle edit)
  await open(P1);
  await p.click('#pg-editor [data-block-id="im1"] img'); await sleep(150);
  const selOk = await p.evaluate(() => { const s = HKPages.k.editor().editor().state.selection; return !!s.node && s.node.type.name === 'image'; });
  ok('Klick aufs Bild waehlt den Bild-Block (Knotenauswahl)', selOk);
  // Pruefer: Tippen bei ausgewaehltem Bild ersetzt es nicht (wie hkGuard bei Karten; Bild ist kein atom)
  await p.keyboard.type('q'); await sleep(200);
  const keep = await p.evaluate(() => { const s = HKPages.k.editor().editor().state.selection; return { img: !!document.querySelector('#pg-editor [data-block-id="im1"] img'), sel: !!s.node && s.node.type.name === 'image' }; });
  ok('Ausgewaehltes Bild + Tippen: Bild bleibt, Auswahl bleibt', keep.img && keep.sel, keep);
  const clip = await p.evaluate(() => {
    const dt = new DataTransfer(), e = new ClipboardEvent('copy', { clipboardData: dt, bubbles: true, cancelable: true });
    HKPages.k.editor().editor().view.dom.dispatchEvent(e);
    return { json: dt.getData('application/x-editor-js'), html: dt.getData('text/html'), txt: dt.getData('text/plain') };
  });
  const cj = JSON.parse(clip.json || '[]');
  ok('Cmd+C auf Bild: Saga-Block (mit Unterschrift), HTML mit data-saga-block, Text = Adresse', cj.length === 1 && cj[0].tool === 'image' && /fett/.test(cj[0].data.caption) && /data-saga-block/.test(clip.html) && /\/files\//.test(clip.txt), clip);
  if (CK2 && HANDLE2) {
    const Bs = await session(CK2), pb = Bs.p;
    await D.setEditor(pb, 'tiptap');
    const PB = (await pb.api('POST', '/api/notes', { title: 'TM Ziel B ' + RUN })).id;
    await pb.api('PATCH', '/api/notes/' + PB, { body: J({ blocks: [{ id: 'zb', type: 'paragraph', data: { text: 'Ziel' } }] }), format: 'blocks' });
    await pb.api('PUT', '/api/notes/' + PB + '/shares', { type: 'user', target: process.env.HANDLE1 || 'hk-test-a', role: 'edit' });
    // Seite von A mit B teilen (Quelle sehen)
    await p.api('PUT', '/api/notes/' + P1 + '/shares', { type: 'user', target: HANDLE2, role: 'view' });
    await open(PB);
    const acc = await p.evaluate(() => HKPages.k.cur() && HKPages.k.cur().role);
    await D.focus(p, 'zb', 'end');
    await p.evaluate((j) => { const dt = new DataTransfer(); dt.setData('application/x-editor-js', j); HKPages.k.editor().editor().view.dom.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }, clip.json);
    await sleep(2200);
    const bb = JSON.parse((await pb.api('GET', '/api/notes/' + PB)).body || '{}').blocks || [];
    const cimg = bb.find((x) => x.type === 'image');
    ok('Einfuegen auf fremder Seite (Rolle ' + acc + '): Bild mit neuem Dateiverweis (Kopie fuer den Eigentuemer), Unterschrift bleibt',
      cimg && /^\/files\/[0-9a-f]{24}$/.test(cimg.data.file.url) && cimg.data.file.url !== up1.file.url && /fett/.test(cimg.data.caption) && cimg.id !== 'im1', [acc, bb]);
    // Nextcloud-Block einer anderen Person einfuegen: abgewiesen
    const ncClip = J([{ tool: 'nextcloud', data: Object.assign({}, MEDIA[4].data, { author: 'jemand-anders', fileid: '77' }) }]);
    await D.focus(p, 'zb', 'end');
    await p.evaluate((j) => { const dt = new DataTransfer(); dt.setData('application/x-editor-js', j); HKPages.k.editor().editor().view.dom.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); }, ncClip);
    await sleep(1800);
    const bb2 = JSON.parse((await pb.api('GET', '/api/notes/' + PB)).body || '{}').blocks || [];
    ok('Fremder Nextcloud-Block wird beim Einfuegen abgewiesen', !bb2.some((x) => x.type === 'nextcloud') && !(await p.$('#pg-editor .hk-tool-nextcloud')), bb2.map((x) => x.type));
    // 6) Rolle Lesen: B sieht Seite P1 (view)
    await open(P1, pb);
    const rv = await pb.evaluate(() => ({
      ce: document.querySelector('#pg-editor .pm-doc').getAttribute('contenteditable'),
      img: !!document.querySelector('[data-block-id="im1"] img'), rs: document.querySelectorAll('[data-block-id="im1"] .hk-rs').length,
      att: !!document.querySelector('.hk-tool-attaches .pm-att-in'), attT: (document.querySelector('.hk-tool-attaches .pm-att-t') || {}).textContent,
      yt: !!document.querySelector('[data-block-id="em1"] iframe'), bar: !!document.querySelector('[data-block-id="em2"] .hk-embed-bar'),
      cards: document.querySelectorAll('#pg-editor .pm-card').length
    }));
    await pb.click('[data-block-id="im1"] [data-pm-part="icap"]'); await pb.keyboard.type('xx'); await sleep(1300);
    ok('Rolle Lesen: Medien angezeigt, keine Griffe/Eingabefelder/Leisten, nicht bearbeitbar', rv.ce === 'false' && rv.img && rv.rs === 0 && !rv.att && rv.attT === 'Neuer Titel' && rv.yt && !rv.bar && rv.cards === 0, rv);
    ok('Rolle Lesen: Tippen in die Unterschrift aendert nichts, kein PATCH', pb.patches === 0 && /fett<\/b>$/.test(find(await get(P1), 'im1').data.caption), pb.patches);
    const slB = await pb.evaluate(() => HKPmEditor.slash.list().filter((x) => x.when && x.key === 'media-immich').map((x) => x.when()));
    ok('Rolle Lesen: keine Seitenfehler', !pb.errs.length, pb.errs);
    // Immich nur fuer den Eigentuemer: auf der fremden Seite PB (B Eigentuemer, A bearbeitet) fehlt der Eintrag fuer A
    await D.focus(p, 'zb', 'end'); await p.keyboard.type(' '); await slash('Immich');
    const imA = await p.evaluate(() => [...document.querySelectorAll('.pm-pop:not([hidden]) .hk-at-t')].map((x) => x.textContent));
    await p.keyboard.press('Escape');
    ok('"Bild aus Immich" nur fuer den Eigentuemer (fremde Seite: kein Eintrag)', !imA.includes('Bild aus Immich') && slB.length === 1, [imA, slB]);
    await pb.api('DELETE', '/api/notes/' + PB).catch(() => {});
    await Bs.ctx.close();
  }

  // 7) Immich: Eintrag oeffnet die Auswahl, gewaehltes Bild wird Bild-Block (Auswahl im Test ersetzt)
  const P3 = await mk('TM Immich ' + RUN, [{ id: 'i0', type: 'paragraph', data: { text: 'x' } }]);
  await open(P3);
  await p.evaluate((u) => { HK.immichPick = (onPick) => setTimeout(() => onPick(u, 'foto.jpg'), 50); }, up1.file.url);
  await D.focus(p, 'i0', 'end'); await p.keyboard.type(' '); await slash('Immich'); await p.keyboard.press('Enter');
  b = await saved(P3);
  ok('Immich: gewaehltes Bild -> Bild-Block unter dem Absatz (Editor.js-Form)', b.length === 2 && b[1].type === 'image' && b[1].data.file.url === up1.file.url && b[1].data.caption === '', b);

  // 8) Nextcloud ueber die Bruecke: Signatur bleibt bei fremden Aenderungen am Rest der Seite; neuer leerer Block nicht gespeichert
  await open(P1);
  await D.focus(p, 'pz', 'end'); await p.keyboard.type(' mehr', { delay: 10 });
  b = await saved(P1);
  ok('Nextcloud: nach Bearbeiten anderer Bloecke unveraendert (sig, author, alle Felder)', J(find(b, 'nc1')) === J(find(orig, 'nc1')), find(b, 'nc1'));
  const bridge = await p.evaluate(() => { const t = document.querySelector('.hk-tool-nextcloud'); return !!(t && t.hkTool && t.hkTool.instance && t.hkTool.instance()); });
  ok('Nextcloud: Node-View ist die Bruecke (NextcloudTool)', bridge);

  // 9) Speicher/CSP/Fehler
  ok('Keine externen Netzaufrufe ausser abgefangenen (YouTube-Player)', ext.every((u) => /youtube-nocookie\.com/.test(u)), ext);
  const csp = await p.evaluate(() => window.__csp);
  ok('CSP-Verletzungen = 0 (securitypolicyviolation)', !csp.length, csp);
  ok('Keine Seitenfehler (pageerror)', !p.errs.length, p.errs);
  await D.setEditor(p, was.tiptap ? 'tiptap' : 'editorjs');
  for (const id of made) await p.api('DELETE', '/api/notes/' + id).catch(() => {});
  await br.close();
  const n = res.filter(Boolean).length;
  console.log(`\nT4a Medien ${n}/${res.length} ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
  process.exit(n === res.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
