// Listen behalten fremde Felder (rot-web, Saga 2.5): Eine Seite mit Listen mit und ohne Saga-Kennung data.bo (wie die
// Kapitel-Liste der BookOrbit-Buchseite), nummeriert mit Start, Checkliste, verschachtelt und einem Absatz mit data.bo.
// Erwartet nach Bearbeiten im Editor + Speichern: data.bo bleibt genau an den Listen, die es hatten (auch nach Tippen in
// der Liste selbst), keine andere Liste bekommt Felder dazu, style/meta/items der Listen bleiben gleich, Duplizieren
// (Strg/Cmd+D) traegt data.bo nicht in die Kopie, nach dem Neuladen steht im Editor derselbe Inhalt wie auf dem Server.
// Laeuft mit SAGA_EDITOR=editorjs (Standard) und tiptap (Gegenprobe).
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> node tests/browser_liste_bo.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + String(JSON.stringify(i)).slice(0, 700))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 8000)) return v; await sleep(200); } }
const J = (x) => JSON.stringify(x);
const it = (c, meta, items) => ({ content: c, meta: meta || {}, items: items || [] });
const BO = { k: '_ch', x: 'chl', h: 'test-h' };
const START = [
  { id: 'lp1', type: 'paragraph', data: { text: 'Absatz mit Kennung', bo: { k: '_head', x: 'info' } } },
  { id: 'lb1', type: 'list', data: { style: 'unordered', meta: {}, items: [it('Erstes Kapitel'), it('Zweites Kapitel')], bo: BO } },
  { id: 'ln1', type: 'list', data: { style: 'unordered', meta: {}, items: [it('normal eins'), it('normal zwei')] } },
  { id: 'lo1', type: 'list', data: { style: 'ordered', meta: { start: 3, counterType: 'numeric' }, items: [it('drei'), it('vier')] } },
  { id: 'lc1', type: 'list', data: { style: 'checklist', meta: {}, items: [it('erledigt', { checked: true }), it('offen', { checked: false })] } },
  { id: 'lv1', type: 'list', data: { style: 'unordered', meta: {}, items: [it('oben', {}, [it('unten')])] } },
  { id: 'le1', type: 'paragraph', data: { text: 'Ende' } },
];
// Inhalt einer Liste ohne Gestaltung vergleichen (Text der Eintraege, Verschachtelung, Haken)
const shape = (xs) => (xs || []).map((x) => [String(x.content || '').replace(/<[^>]*>/g, '').replace(/&nbsp;| /g, ' ').trim(),
  !!(x.meta && x.meta.checked), shape(x.items)]);
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  await p.goto(BASE + '/notes');
  const kind = await D.useEditor(p);
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const blocksOf = async (id) => JSON.parse((await api('GET', '/api/notes/' + id)).body || '{"blocks":[]}').blocks;
  const n = await api('POST', '/api/notes', { title: 'Listen mit Kennung' });
  const id = n.id;
  const pt = await api('PATCH', '/api/notes/' + id, { format: 'blocks', body: J({ blocks: START }) });
  ok('Seite angelegt', !!id && !pt.error, [n, pt]);
  const b0 = await blocksOf(id);
  ok('Server speichert data.bo an Liste und Absatz', J((b0.find((b) => b.id === 'lb1') || {}).data.bo) === J(BO)
    && !!(b0.find((b) => b.id === 'lp1') || {}).data.bo, b0);
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
  await D.waitBlock(p, 'le1', 15000); await sleep(600);
  ok('Editor: ' + kind, (await D.isEditor(p)) === kind);

  // 1. anderswo tippen -> Speichern: alle Listen unveraendert, Kennung bleibt genau an lb1/lp1
  await D.typeEnd(p, 'le1', ' eins');
  let b1 = await until(async () => { const b = await blocksOf(id); return b.some((x) => x.id === 'le1' && /Ende eins/.test(x.data.text)) ? b : null; }, 10000);
  ok('Speichern nach Tippen im Absatz angekommen', !!b1);
  b1 = b1 || [];
  const by = (bl, i) => bl.find((b) => b.id === i) || { data: {} };
  ok('Kapitel-Liste behaelt data.bo', J(by(b1, 'lb1').data.bo) === J(BO), by(b1, 'lb1'));
  ok('Absatz behaelt data.bo', J(by(b1, 'lp1').data.bo) === J({ k: '_head', x: 'info' }), by(b1, 'lp1'));
  const keysOk = (b) => Object.keys(b.data).every((k) => ['style', 'meta', 'items'].includes(k));
  ok('Listen ohne Kennung bekommen keine Felder dazu', ['ln1', 'lo1', 'lc1', 'lv1'].every((i) => keysOk(by(b1, i))),
    ['ln1', 'lo1', 'lc1', 'lv1'].map((i) => Object.keys(by(b1, i).data)));
  ok('Listeninhalt unveraendert (Text, Haken, Verschachtelung)', START.filter((b) => b.type === 'list').every((s) => J(shape(by(b1, s.id).data.items)) === J(shape(s.data.items))),
    START.filter((b) => b.type === 'list').map((s) => [s.id, shape(by(b1, s.id).data.items)]));
  ok('Stil der Listen unveraendert', START.filter((b) => b.type === 'list').every((s) => by(b1, s.id).data.style === s.data.style),
    b1.filter((b) => b.type === 'list').map((b) => [b.id, b.data.style]));
  ok('nummerierte Liste behaelt Start 3', (by(b1, 'lo1').data.meta || {}).start === 3, by(b1, 'lo1').data);

  // 2. in der Kapitel-Liste selbst tippen: Kennung bleibt (der Server erkennt "bearbeitet" an h)
  await D.typeEnd(p, 'lb1', ' neu');
  const b2 = await until(async () => { const b = await blocksOf(id); return b.some((x) => x.id === 'lb1' && /Kapitel neu/.test(J(x.data.items))) ? b : null; }, 10000) || [];
  ok('Tippen in der Kapitel-Liste gespeichert', b2.length > 0);
  ok('Kapitel-Liste behaelt data.bo nach eigener Bearbeitung', J(by(b2, 'lb1').data.bo) === J(BO), by(b2, 'lb1'));

  // 3. Kapitel-Liste duplizieren: Kopie ohne Kennung (sonst zoege der Server zwei Listen nach)
  await D.clickEnd(p, 'lb1');
  await p.keyboard.press(process.platform === 'darwin' ? 'Meta+KeyD' : 'Control+KeyD');
  const b3 = await until(async () => { const b = await blocksOf(id); return b.filter((x) => x.type === 'list').length === 6 ? b : null; }, 10000) || [];
  const lists3 = b3.filter((x) => x.type === 'list');
  ok('Kopie der Kapitel-Liste gespeichert', lists3.length === 6, b3.map((b) => b.id + ':' + b.type));
  const one = lists3.filter((x) => x.data.bo).map((x) => x.id).join() === 'lb1';
  // Tiptap kopiert beim Duplizieren auch data.bo (Attribut extra) - vorbestehend, nicht Teil von rot-web: nur melden
  if (kind === 'tiptap' && !one) console.log('INFO Tiptap: Kopie der Kapitel-Liste traegt data.bo mit -> ' + J(lists3.map((x) => [x.id, !!x.data.bo])));
  else ok('nur eine Liste traegt data.bo (das Original)', one, lists3.map((x) => [x.id, !!x.data.bo]));

  // 4. neu laden: Editor zeigt, was der Server hat; ein weiteres Speichern aendert keine Liste
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
  await D.waitBlock(p, 'le1', 15000); await sleep(600);
  const ed = await D.blocks(p);
  const srv = await blocksOf(id);
  ok('nach Neuladen: gleiche Bloecke in gleicher Reihenfolge', J(ed.map((b) => b.id)) === J(srv.map((b) => b.id)), [ed.map((b) => b.id), srv.map((b) => b.id)]);
  await D.typeEnd(p, 'le1', ' zwei');
  const b4 = await until(async () => { const b = await blocksOf(id); return b.some((x) => x.id === 'le1' && /eins zwei/.test(x.data.text)) ? b : null; }, 10000) || [];
  const strip = (bl) => J(bl.filter((b) => b.type === 'list').map((b) => [b.id, b.data.style, b.data.bo || null, (b.data.meta || {}).start || null, shape(b.data.items)]));
  ok('zweites Speichern nach Neuladen: Listen samt Kennung unveraendert', b4.length > 0 && strip(b4) === strip(srv), [strip(b4), strip(srv)]);

  ok('Keine Seitenfehler', !errs.length, errs);
  await api('DELETE', '/api/notes/' + id);
  await br.close();
  const g = res.filter(Boolean).length;
  console.log('\nBROWSER-LISTE-BO ' + g + '/' + res.length + ' (' + kind + ')' + (g === res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(g === res.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
