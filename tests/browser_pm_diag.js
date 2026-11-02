// Editor-Diagnose (2.5, Tiptap-Umbau T6, static/pm-diag.js + POST /api/diag): Seite mit allen Blocktypen (auch
// Altformate, fremde Felder, Verweis/Erwaehnung/Datum/Formel) laden, nichts aendern, speichern -> keine Meldung, kein
// POST /api/diag. Eine Bearbeitung meldet nichts (angefasster Block zaehlt nicht). Kuenstliche Abweichung ueber den
// Test-Haken window.__hkDiagTamper (verfaelscht die frische Serialisierung) -> genau ein POST mit genau einer Meldung
// (Typ + Feldpfad + Art), ohne Inhalt, Text, IDs, Dateinamen, URLs. Dedupliziert (zweite Pruefung: kein neuer Eintrag).
// Nur mit SAGA_EDITOR=tiptap (Admin editor.engine = choice, editor.diag an; der Test stellt die Konto-Wahl selbst um).
// Der Server nimmt hoechstens einen POST je 55 s je Konto an: zwei Laeufe mit demselben Konto mindestens 1 min auseinander.
// Mit notes.live (Feature live1) zusaetzlich: Pruefung live einige Sekunden nach einer Aenderung.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> SAGA_EDITOR=tiptap node tests/browser_pm_diag.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);
const J = (x) => JSON.stringify(x);
if (process.env.SAGA_EDITOR !== 'tiptap') { console.log('nur mit SAGA_EDITOR=tiptap'); process.exit(0); }

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1200, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); p.errs = [];
  p.on('pageerror', (e) => p.errs.push(e.message));
  const DIAG = [];                                          // gesendete Bodies an /api/diag
  p.on('request', (r) => { if (r.method() === 'POST' && /\/api\/diag$/.test(r.url())) DIAG.push(r.postData() || ''); });
  const CODES = []; p.on('response', (r) => { if (/\/api\/diag$/.test(r.url())) CODES.push(r.status()); });
  await p.goto(BASE + '/notes'); await sleep(600);
  p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
  const was = await p.evaluate(() => HK.api('GET', '/api/account/editor'));
  ok('Schalter: Admin erlaubt die Wahl (editor.engine = choice)', was.choice === true, was);
  const feats = (await p.api('GET', '/api/version')).features || [];
  ok('Feature pmdiag1 an', feats.includes('pmdiag1'), feats);
  await D.setEditor(p, 'tiptap');
  const made = [];
  const mk = async (title, blocks) => { const id = (await p.api('POST', '/api/notes', { title })).id; made.push(id); await p.api('PATCH', '/api/notes/' + id, { body: J({ blocks }), format: 'blocks' }); return id; };
  const get = async (id) => JSON.parse((await p.api('GET', '/api/notes/' + id)).body || '{}').blocks || [];
  async function open(id) {
    await p.goto(BASE + '/notes#' + id); await p.reload();
    for (let i = 0; i < 100; i++) { if (await p.evaluate((id) => window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector('#pg-editor .pm-doc') && window.HKPmDiag, id)) break; await sleep(50); }
    await sleep(800);
  }
  const ds = () => p.evaluate(() => HKPmDiag.state());
  const edSave = () => p.evaluate(() => HKPages.k.editor().save().then((o) => JSON.stringify(o.blocks)));

  // ---------------------------------------------------------------------------------------------------------------
  // 1) alle Blocktypen, nichts aendern -> keine Meldung
  const P0 = await mk('PD Ziel ' + RUN, [{ id: 'z1', type: 'paragraph', data: { text: 'Ziel' } }]);
  const ALL = [
    { id: 'p1', type: 'paragraph', data: { text: 'Hallo <strong>Welt</strong> <em>k</em> <a href="https://example.org/x">Link</a> <a href="/notes#' + P0 + '">Ziel</a> ' +
      '<span class="hk-mention" data-u="hk-test-b">Bert Test</span> <time datetime="2026-11-02" class="hk-date">2. Nov.</time> <code class="hk-math">a^2</code> ' +
      '<mark class="cdx-marker">m</mark> <mark class="hk-bg-yellow">g</mark> <span class="hk-c-red">r</span><br>neu' } },
    { id: 'h1', type: 'header', data: { text: 'Titel', level: 2 }, tunes: { hkstyle: { c: 'blue' } } },
    { id: 'q1', type: 'quote', data: { text: 'Zitat', caption: 'Quelle', alignment: 'left' } },
    { id: 'k1', type: 'callout', data: { emoji: '💡', text: 'Hinweis <u>u</u>' } },
    { id: 'd1', type: 'delimiter', data: {} },
    { id: 'l1', type: 'list', data: { style: 'checklist', meta: {}, items: [{ content: 'a', meta: { checked: true }, items: [{ content: 'b', meta: { checked: false }, items: [] }] }] } },
    { id: 'l2', type: 'list', data: { style: 'unordered', meta: {}, items: ['alt als Text', { content: 'neu', items: ['innen'], meta: {} }] } },
    { id: 'c1', type: 'code', data: { code: 'x = 1\ny = 2', language: 'python' } },
    { id: 't1', type: 'toggle', data: { title: 'Aufklappen', open: true, blocks: [{ id: 't1a', type: 'paragraph', data: { text: 'innen' } }] } },
    { id: 't2', type: 'toggle', data: { text: 'Altformat', open: false } },
    { id: 'cl1', type: 'columns', data: { cols: [{ blocks: [{ id: 'cc1', type: 'paragraph', data: { text: 'L' } }] }, { w: 0.4, blocks: [{ id: 'cc2', type: 'header', data: { text: 'R', level: 3 } }] }] } },
    { id: 'tb1', type: 'table', data: { withHeadings: true, colWidths: [40, 60], content: [['<b>a</b>', 'b'], ['c', '<i>d</i>']], zz: { fremd: 1 } } },
    { id: 'm1', type: 'math', data: { tex: '\\frac{a}{b}' } },
    { id: 'w1', type: 'widget', data: { kind: 'clock', v: 1, cfg: { tz: '', h24: true, sec: false, analog: false } } },
    { id: 'em1', type: 'embed', data: { url: 'https://example.org/artikel', title: 'Ein Artikel' } },
    { id: 'u1', type: 'checklist', data: { items: [{ text: 'alt', checked: true }] } },
    { id: 'pz', type: 'paragraph', data: { text: 'Ende' } }
  ];
  const P1 = await mk('PD Alle ' + RUN, ALL);
  const o1 = await get(P1);
  await open(P1);
  ok('Editor ist Tiptap, keine Seitenfehler', await D.isEditor(p) === 'tiptap' && !p.errs.length, p.errs);
  ok('Ohne Bearbeitung: save() byte-gleich mit GET', (await edSave()) === J(o1));
  await sleep(300);
  let st = await ds();
  ok(`Speichern ohne Aenderung: Bloecke geprueft, keine Meldung (${st.checked} geprueft, ${st.found} gefunden)`, st.checked >= ALL.length && st.found === 0 && !st.queued, st);
  // eine Bearbeitung: angefasster Block meldet nichts
  await D.typeEnd(p, 'pz', ' mehr'); await sleep(1800);
  const o1b = await get(P1);
  ok('Bearbeitung gespeichert', (o1b.find((b) => b.id === 'pz') || {}).data.text === 'Ende mehr', o1b.slice(-1));
  st = await ds();
  ok(`nach Bearbeitung + Speichern: weiter keine Meldung (${st.found})`, st.found === 0 && !st.queued, st);
  await p.evaluate(() => HKPmDiag.flush()); await sleep(400);
  ok('kein POST /api/diag ohne Abweichung', DIAG.length === 0, DIAG);

  // ---------------------------------------------------------------------------------------------------------------
  // 2) kuenstliche Abweichung (Test-Haken): genau eine Meldung, ohne Inhalt
  const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
  const up = await p.evaluate(async (b64) => {
    const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)), fd = new FormData();
    fd.append('image', new File([bin], 'urlaub-geheim.png', { type: 'image/png' }));
    return (await fetch('/api/files', { method: 'POST', headers: { 'X-Saga': '1' }, body: fd })).json();
  }, PNG.toString('base64'));
  const P2 = await mk('PD Abw ' + RUN, [
    { id: 'tx1', type: 'table', data: { withHeadings: false, content: [['geheim-a', 'geheim-b'], ['geheim-c', 'geheim-d'], ['x', 'GEHEIM-ZELLE']] } },
    { id: 'px1', type: 'paragraph', data: { text: 'geheimer Text' } },
    { id: 'im1', type: 'image', data: { file: { url: up.file.url, name: 'urlaub-geheim.png' }, caption: 'Bildtext', withBorder: false, withBackground: false, stretched: false } }
  ]);
  await open(P2);                                      // Haken nach dem Laden (reload setzt window zurueck)
  ok('ohne Haken: auch hier keine Meldung', (await ds()).found === 0, await ds());
  await p.evaluate(() => { HKPmDiag.reset(); window.__hkDiagTamper = (t, b) => { if (t === 'table' && b.data && b.data.content) b.data.content[2][1] = 'verfaelscht'; return b; }; });
  await p.evaluate(() => HKPages.k.editor().save()); await sleep(800);
  st = await ds();
  ok(`Abweichung erkannt: ein Fund (${st.found})`, st.found === 1, st);
  await p.evaluate(() => HKPages.k.editor().save()); await sleep(300);
  ok('zweites Speichern: dedupliziert (kein weiterer Fund)', (await ds()).found === 1, await ds());
  await p.evaluate(() => HKPmDiag.flush()); await sleep(800);
  ok(`genau ein POST /api/diag, angenommen (${DIAG.length}, ${CODES}; 429 = Lauf vor < 55 s mit demselben Konto)`, DIAG.length === 1 && J(CODES) === '[200]', CODES);
  let sent = {}; try { sent = JSON.parse(DIAG[0] || '{}'); } catch (e) { /* unten FAIL */ }
  ok(`POST: v 1, mode save|live, sv, genau ein Eintrag table / data.content[2][1] / fresh (${J(sent.items)})`,
    sent.v === 1 && /^(save|live)$/.test(sent.mode) && typeof sent.sv === 'number' && J(sent.items) === J([{ t: 'table', p: 'data.content[2][1]', k: 'fresh' }]), sent);
  const raw = DIAG.join('\n');
  ok('POST ohne Inhalt, Text, IDs, Dateinamen, URLs', !/geheim|GEHEIM|verfaelscht|tx1|px1|im1|urlaub|\/files\/|example|Bildtext|https?:/.test(raw), raw);
  st = await ds();
  ok(`Sendeabstand: Status zeigt einen POST, Warteschlange leer (${st.posts}, ${st.queued})`, st.posts === 1 && st.queued === 0, st);
  await p.evaluate(() => { window.__hkDiagTamper = null; });

  // ---------------------------------------------------------------------------------------------------------------
  // 3) Live (nur wenn notes.live an, Feature live1): Pruefung einige Sekunden nach einer Aenderung, ohne Speichern
  if (feats.includes('live1')) {
    const P3 = await mk('PD Live ' + RUN, ALL.filter((b) => b.type !== 'toggle' || !('text' in b.data)));
    await open(P3);
    let live = false;
    for (let i = 0; i < 60 && !live; i++) { live = await p.evaluate(() => !!(window.HKPmLive && HKPmLive.state().live)); if (!live) await sleep(150); }
    ok('Live gebunden', live);
    await p.evaluate(() => { HKPmDiag.reset(); window.__hkDiagTamper = null; });
    await D.typeEnd(p, 'pz', ' live'); await sleep(5500);
    st = await ds();
    ok(`Live: Pruefung nach Aenderung, unveraenderte Bloecke ohne Meldung (${st.checked} geprueft, ${st.found})`, st.checked >= 10 && st.found === 0, st);
    await p.evaluate(() => { HKPmDiag.reset(); window.__hkDiagTamper = (t, b) => { if (t === 'quote' && b.data) b.data.alignment = 'right'; return b; }; });
    await D.typeEnd(p, 'pz', '!'); await sleep(5500);
    st = await ds();
    ok(`Live: kuenstliche Abweichung -> quote / data.alignment / fresh (${J(st.items)})`, st.found === 1 && J(st.items[0] || {}) === J({ t: 'quote', p: 'data.alignment', k: 'fresh' }), st);
    await p.evaluate(() => { window.__hkDiagTamper = null; });
  } else console.log('(Live-Teil uebersprungen: notes.live aus)');

  // Schalter aus (Feature fehlt -> Client prueft und sendet nicht; Server 404) prueft tests/pm_diag.py
  ok('keine Seitenfehler', !p.errs.length, p.errs);
  for (const id of made) await p.api('DELETE', '/api/notes/' + id).catch(() => {});
  await br.close();
  console.log(`\nBROWSER-PM-DIAG ${res.filter(Boolean).length}/${res.length}`, res.every(Boolean) ? '- ALLES GRUEN' : '- FEHLER');
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
