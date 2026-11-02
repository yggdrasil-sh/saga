// Saga 2.5 P3-E7 im Web: live mitschreiben ueber Servergrenzen (collab1). Anna (Server A, Eigentuemerin) und Bert
// (Server B, "Bearbeiten", negative ID) mit je einem Browser an derselben Seite (gesteuert von tests/browser_fed_live.py).
// Geprueft: beide live (static/pm-live.js, Tiptap), gleichzeitiges Tippen im selben Absatz konvergiert, fremder Cursor
// mit Anzeigenamen, kein PATCH des Inhalts, materialisierter body beim Eigentuemer, Rechte-Entzug (Lesen) -> Bert nur
// lesen, keine Fehler/CSP-Verstoesse. Umgebung: BASE_A, BASE_B, CK_A, CK_B (Cookie-Werte), NID (ID auf A), RID (auf B).
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE_A = process.env.BASE_A, BASE_B = process.env.BASE_B, NID = +process.env.NID, RID = +process.env.RID;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 9000) { const end = Date.now() + ms; while (Date.now() < end) { try { if (await fn()) return true; } catch (e) { /* weiter */ } await sleep(150); } return false; }
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const mk = async (base, ck) => {
    const ctx = await br.newContext({ viewport: { width: 1200, height: 900 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: ck, domain: new URL(base).hostname, path: '/' }]);
    const p = await ctx.newPage(); p.errs = []; p.base = base; p.patches = [];
    p.on('pageerror', (e) => p.errs.push(e.message));
    await p.addInitScript(() => { window.__csp = []; document.addEventListener('securitypolicyviolation', (e) => window.__csp.push(e.violatedDirective)); });
    p.on('request', (r) => { if (r.method() === 'PATCH' && /\/api\/notes\/-?\d+$/.test(r.url())) p.patches.push(r.postData() || ''); });
    await p.goto(base + '/notes'); await sleep(400);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return p;
  };
  const a = await mk(BASE_A, process.env.CK_A), b = await mk(BASE_B, process.env.CK_B);
  await D.setEditor(a, 'tiptap'); await D.setEditor(b, 'tiptap');
  const st = (p) => p.evaluate(() => window.HKPmLive ? HKPmLive.state() : null);
  async function open(p, id) {
    await p.goto(p.base + '/notes#' + id); await p.reload();
    for (let i = 0; i < 200; i++) {
      const s = await p.evaluate((id) => window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector('#pg-editor .pm-doc') && window.HKPmLive ? HKPmLive.state() : null, id);
      if (s && s.mode === 'live') break;
      await sleep(50);
    }
    await sleep(300); p.patches = [];
  }
  const txt = (p, id) => p.evaluate((id) => { const ed = HKPages.k.editor().editor(); let t = null; ed.state.doc.descendants((n) => { if (t === null && n.attrs && n.attrs.id === id) t = n.textContent; return t === null; }); return t; }, id);
  await open(a, NID); await open(b, RID);
  const sa = await st(a), sb = await st(b);
  ok('Beide live (A Eigentuemerin, B fremde Seite mit Bearbeiten)', sa && sb && sa.mode === 'live' && sb.mode === 'live' && sb.role === 'edit' && !sb.ro && sb.nid === RID, [sa, sb]);

  await D.focus(a, 'p1', 'end'); await D.focus(b, 'p1', 'start');
  await Promise.all([a.keyboard.type(' von Anna', { delay: 30 }), b.keyboard.type('Bert: ', { delay: 30 })]);
  const conv = await until(async () => (await txt(a, 'p1')) === (await txt(b, 'p1')) && /von Anna/.test(await txt(b, 'p1')) && /Bert: /.test(await txt(a, 'p1')), 12000);
  ok(`Gleichzeitig im selben Absatz ueber Servergrenzen: beide gleich (${await txt(a, 'p1')})`, conv, [await txt(a, 'p1'), await txt(b, 'p1')]);
  ok('... Text ist "Bert: Hallo von Anna"', (await txt(a, 'p1')) === 'Bert: Hallo von Anna', await txt(a, 'p1'));
  const car = await until(() => a.evaluate(() => [...document.querySelectorAll('#pg-editor .hk-caret')].some((c) => /Bert/.test(c.textContent))), 8000);
  ok('Anna sieht Berts Cursor (Anzeigename, anderer Server)', car);
  const carB = await until(() => b.evaluate(() => [...document.querySelectorAll('#pg-editor .hk-caret')].some((c) => /Anna/.test(c.textContent))), 8000);
  ok('Bert sieht Annas Cursor', carB);
  ok('kein Anmeldename im Editor', !(await a.evaluate(() => document.querySelector('#pg-editor').innerHTML)).includes('fb-bert')
     && !(await b.evaluate(() => document.querySelector('#pg-editor').innerHTML)).includes('fa-anna'));
  ok('Live: kein PATCH des Inhalts (beide Seiten)', !a.patches.some((x) => /"body"/.test(x)) && !b.patches.some((x) => /"body"/.test(x)), [a.patches, b.patches]);
  const mat = await until(async () => JSON.parse((await a.api('GET', '/api/notes/' + NID)).body).blocks[0].data.text === 'Bert: Hallo von Anna', 8000);
  ok('Eigentuemer: materialisierter body = Stand der Editoren', mat);

  await a.api('PUT', '/api/notes/' + NID + '/shares', { type: 'remote', target: process.env.BERT, role: 'view' });
  const ro = await until(async () => { const s = await st(b); return s && s.ro; }, 15000);
  ok('Rolle Lesen beim Eigentuemer -> Bert nur lesen', ro, await st(b));
  ok('... Editor bei Bert nicht bearbeitbar', await b.evaluate(() => { const e = document.querySelector('#pg-editor .ProseMirror'); return e && e.getAttribute('contenteditable') === 'false'; }));
  const csp = (await a.evaluate(() => window.__csp.length)) + (await b.evaluate(() => window.__csp.length));
  ok(`Keine Fehler/CSP-Verstoesse (${a.errs.concat(b.errs).join(' / ')})`, !a.errs.length && !b.errs.length && !csp);
  await br.close();
  console.log(`\nFED-LIVE-WEB ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
