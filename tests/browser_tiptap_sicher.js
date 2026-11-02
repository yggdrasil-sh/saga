// 2.5 (Sicherheit, Funde sec-web #2/#4/#5): Tiptap-Editor laedt aus fremder Hand keine Adressen dieses Servers mit der
// Sitzung (GET /logout, /lang?l=..) und uebernimmt keine unsicheren Links.
// - Einfuegen von Saga-HTML (data-block-type + data-pm) mit Bild /logout, /lang?l=th, Vorschaubild /logout, Datei-Link
//   /logout, Link-Marke javascript:/data: -> keine Anfrage an /logout oder /lang (Netzwerk-Mitschnitt), weiter angemeldet,
//   Kontosprache unveraendert, Bildblock ohne src (Platzhalter), Link ohne href.
// - Eigene Dateien (/files/<id>) bleiben (Kopieren/Einfuegen wie bisher).
// - Link-Marke: Einfuegen von <a href="javascript:..."> -> nur Text; Link setzen mit javascript: -> kein Link.
// - Live (notes.live an): kaputte Awareness eines zweiten Clients (Cursor-Muell) -> kein pageerror beim anderen.
// - Abmelden: Menue "Abmelden" (POST) meldet ab; GET /logout allein meldet nicht ab.
// Lokal: HK_TEST_BASE CK CK2 SAGA_EDITOR=tiptap (Admin editor.engine = choice, fuer den Live-Teil notes.live an).
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);
const J = (x) => JSON.stringify(x);
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==', 'base64');

(async () => {
  if (process.env.SAGA_EDITOR !== 'tiptap') { console.log('Nur mit SAGA_EDITOR=tiptap'); process.exit(2); }
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  async function session(ck) {
    const ctx = await br.newContext({ viewport: { width: 1200, height: 900 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: ck, domain: new URL(BASE).hostname, path: '/' }]);
    await ctx.route((u) => !u.href.startsWith(BASE), (r) => r.fulfill({ status: 204, body: '' }));
    const p = await ctx.newPage(); p.errs = []; p.reqs = [];
    p.on('pageerror', (e) => p.errs.push(e.message));
    p.on('request', (r) => p.reqs.push(r.method() + ' ' + r.url().replace(BASE, '') + ' ' + r.resourceType()));
    await p.goto(BASE + '/notes'); await sleep(600);
    p.api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    return { ctx, p };
  }
  const A = await session(CK), p = A.p;
  await D.setEditor(p, 'tiptap');
  const made = [];
  const mk = async (title, blocks, pg = p) => { const id = (await pg.api('POST', '/api/notes', { title })).id; made.push(id); if (blocks) await pg.api('PATCH', '/api/notes/' + id, { body: J({ blocks }), format: 'blocks' }); return id; };
  const get = async (id) => JSON.parse((await p.api('GET', '/api/notes/' + id)).body || '{}').blocks || [];
  async function open(id, pg = p) {
    await pg.goto(BASE + '/notes#' + id); await pg.reload();
    for (let i = 0; i < 100; i++) { if (await pg.evaluate((id) => window.HKPages && HKPages.k.cur() && HKPages.k.cur().id === id && document.querySelector('#pg-editor .pm-doc'), id)) break; await sleep(50); }
    await sleep(700);
  }
  const lang0 = (await p.api('GET', '/api/account')).lang;
  await p.api('PUT', '/api/account/lang', { lang: 'de' });
  const up = await p.evaluate(async (b64) => {
    const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)), fd = new FormData();
    fd.append('image', new File([bin], 'x.png', { type: 'image/png' }));
    return (await fetch('/api/files', { method: 'POST', headers: { 'X-Saga': '1' }, body: fd })).json();
  }, PNG.toString('base64'));

  // --- 1) Einfuegen von praepariertem Saga-HTML ----------------------------------------------------------------
  const P1 = await mk('Sicher ' + RUN, [{ id: 'p0', type: 'paragraph', data: { text: 'Start' } }, { id: 'p1', type: 'paragraph', data: { text: '' } }]);
  await open(P1);
  ok('Editor ist Tiptap', await D.isEditor(p) === 'tiptap');
  const esc = (o) => J(o).replace(/&/g, '&amp;').replace(/'/g, '&#39;');
  const html = [
    `<div data-block-type="image" data-block-id="x1" data-pm='${esc({ file: { url: '/logout' } })}'><figcaption data-pm-part="icap">Bild A</figcaption></div>`,
    `<div data-block-type="image" data-block-id="x2" data-pm='${esc({ file: { url: '/lang?l=th&next=/' } })}'><figcaption data-pm-part="icap">Bild B</figcaption></div>`,
    `<div data-block-type="image" data-block-id="x3" data-pm='${esc({ file: { url: up.file.url } })}'><figcaption data-pm-part="icap">eigen</figcaption></div>`,
    `<div data-block-type="math" data-block-id="x4" data-pm='${esc({ tex: 'x^2', render: { png: '/logout', h: '0123456789abcdef' } })}'></div>`,
    `<div data-block-type="attaches" data-block-id="x5" data-pm='${esc({ data: { file: { url: '/logout', name: 'a.txt' }, title: 'A' } })}'></div>`,
    `<p data-block-type="paragraph" data-block-id="x6">L1 <a href="javascript:window.__pwn=1">js</a> <a href="data:text/html,x">d</a> <a href="https://ok.example/">gut</a></p>`
  ].join('');
  await p.click('#pg-editor [data-block-id="p1"]'); await sleep(150);
  p.reqs.length = 0;
  await p.evaluate((h) => {
    const dt = new DataTransfer(); dt.setData('text/html', h); dt.setData('text/plain', 'x');
    const t = document.querySelector('#pg-editor .ProseMirror');
    t.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  }, html);
  await sleep(1500);
  const bad = p.reqs.filter((r) => / \/(logout|lang)\b/.test(r));
  ok('Einfuegen: keine Anfrage an /logout oder /lang (Netzwerk-Mitschnitt)', bad.length === 0, bad);
  const st = await p.evaluate(() => ({
    srcs: [...document.querySelectorAll('#pg-editor img')].map((i) => i.getAttribute('src')).filter(Boolean),
    empty: document.querySelectorAll('#pg-editor .pm-img-empty').length,
    hrefs: [...document.querySelectorAll('#pg-editor a[href]')].map((a) => a.getAttribute('href')),
    dl: [...document.querySelectorAll('#pg-editor .pm-att-dl')].map((a) => a.getAttribute('href')),
    pwn: window.__pwn
  }));
  ok('Bilder: nur die eigene Datei als src, die praeparierten als Platzhalter', J(st.srcs.filter((s) => !/^blob:|^data:/.test(s))) === J([up.file.url]) && st.empty >= 2, st);
  ok('Links: kein javascript:/data:/logout, https bleibt', !st.hrefs.some((h) => /^(javascript|data):|\/logout/i.test(h)) && st.hrefs.includes('https://ok.example/'), st.hrefs);
  ok('Datei-Link ohne /logout', !st.dl.some((h) => /logout/.test(h || '')), st.dl);
  const acc = await p.api('GET', '/api/account').catch(() => null);
  ok('weiter angemeldet, Kontosprache unveraendert (de)', acc && acc.lang === 'de', acc && acc.lang);
  ok('keine Seitenfehler', p.errs.length === 0, p.errs);

  // --- 2) Link setzen mit javascript: -> kein Link ---------------------------------------------------------------
  const out = await p.evaluate(() => {
    const S = window.HKPmSchema;
    return [S.linkHref('javascript:alert(1)'), S.linkHref(' JaVaScRiPt:x'), S.linkHref('https://a.example'), S.linkHref('mailto:a@b.example'),
            S.linkHref('/logout'), S.linkHref('data:x'), S.linkHref('https://a\u0001b')];
  });
  ok('linkHref: nur http(s)/mailto', J(out) === J(['', '', 'https://a.example', 'mailto:a@b.example', '', '', '']), out);
  const wg = await p.evaluate(() => window.HKPmSchema.cleanPm({ type: 'widget', attrs: { id: 'w1', data: { kind: 'progress',
    cfg: { src: 'db', db: 5, view: '', prop: 'p' }, link: '/logout' } } }).attrs.data);
  ok('cleanPm: Widget-Quelle (cfg.src db) bleibt beim Kopieren, Link-Felder weiter nur http(s)', wg.cfg.src === 'db' && wg.link === '', wg);
  const ad = await p.evaluate(() => window.HKPmAdapter && HKPmAdapter.inlineToHtml
    ? HKPmAdapter.inlineToHtml([{ type: 'text', text: 'x', marks: [{ type: 'link', attrs: { href: 'javascript:1' } }, { type: 'b' }] }]) : null);
  ok('Adapter: Link-Marke javascript: -> nur Text (fett bleibt)', ad === null || ad === '<b>x</b>', ad);

  // --- 3) GET /logout meldet nicht ab, das Menue (POST) schon ------------------------------------------------------
  const g = await p.evaluate(async () => { const r = await fetch('/logout', { redirect: 'manual' }); return r.status; });
  ok('GET /logout (fetch): meldet nicht ab', (await p.api('GET', '/api/account').then(() => true, () => false)), g);
  const img = await p.evaluate(() => new Promise((ok) => { const i = new Image(); i.onload = i.onerror = () => ok(1); i.src = '/logout?x=' + Math.random(); }));
  ok('<img src=/logout>: meldet nicht ab', img && (await p.api('GET', '/api/account').then(() => true, () => false)));
  const l1 = await p.evaluate(() => new Promise((ok) => { const i = new Image(); i.onload = i.onerror = () => ok(1); i.src = '/lang?l=th&next=/'; }));
  ok('<img src=/lang?l=th>: Kontosprache unveraendert', l1 && (await p.api('GET', '/api/account')).lang === 'de');

  // --- 4) Live: kaputte Awareness eines anderen Clients -> kein pageerror -------------------------------------------
  const live = await p.evaluate(() => HK.api('GET', '/api/version').then((v) => (v.features || []).includes('live1')));
  if (live && CK2) {
    const P2 = await mk('Sicher Live ' + RUN, [{ id: 'q1', type: 'paragraph', data: { text: 'Hallo' } }]);
    await p.api('PUT', '/api/notes/' + P2 + '/shares', { type: 'user', target: process.env.HANDLE2 || 'hk-test-b', role: 'edit' });
    await open(P2); await sleep(1500);
    p.errs.length = 0;
    const B = await session(CK2), q = B.p;
    await q.goto(BASE + '/notes'); await sleep(400);
    const sent = await q.evaluate(async (nid) => {
      const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/collab');
      ws.binaryType = 'arraybuffer';
      const vu = (n) => { const o = []; while (n > 127) { o.push((n & 127) | 128); n = Math.floor(n / 128); } o.push(n); return o; };
      const frame = (k, nid, parts) => {             // wie static/pm-live.js: art u8, nid int64 BE, Teile varuint + Bytes
        const h = new Uint8Array(9); h[0] = k; new DataView(h.buffer).setBigInt64(1, BigInt(nid));
        let b = [...h]; for (const x of parts) b = b.concat(vu(x.length), x); return new Uint8Array(b);
      };
      const enc = (o) => [...new TextEncoder().encode(JSON.stringify(o))];
      await new Promise((r) => { ws.onopen = r; setTimeout(r, 3000); });
      ws.send(frame(2, nid, [enc({ schema: window.HKPmSchema ? { model: HKPmSchema.MODEL, ver: HKPmSchema.VERSION, hash: HKPmSchema.hash() } : {} })]));
      await new Promise((r) => setTimeout(r, 800));
      for (const c of [{ anchor: 'nonsense', head: 'x' }, { anchor: {}, head: {} }, { anchor: { item: { client: 1, clock: 1 } }, head: { tname: 'zz' } }]) {
        ws.send(frame(7, nid, [enc({ cursor: c })])); await new Promise((r) => setTimeout(r, 200));
      }
      return true;
    }, P2).catch((e) => String(e));
    await sleep(1500);
    ok('Live: kaputte Awareness -> kein Seitenfehler beim anderen', sent === true && p.errs.length === 0, [sent, p.errs]);
    await B.ctx.close();
  } else console.log('     (Live aus oder ohne CK2: Awareness-Teil uebersprungen)');

  // --- 5) Abmelden ueber das Menue (POST) ----------------------------------------------------------------------
  await p.goto(BASE + '/notes'); await sleep(500);
  for (const id of made) await p.api('DELETE', '/api/notes/' + id).catch(() => {});
  await p.api('PUT', '/api/account/lang', { lang: lang0 || '' });
  await p.click('#hk-user', { force: true }); await p.click('.menu-item:has-text("Abmelden")');
  await p.waitForURL(/\/login/, { timeout: 10000 }).catch(() => {});
  ok('Menue "Abmelden" (POST): abgemeldet, auf der Anmeldeseite', /\/login\?bye=1/.test(p.url()) && (await p.evaluate(() => fetch('/api/account', { headers: { 'X-Saga': '1' } }).then((r) => r.status))) === 401, p.url());
  await br.close();
  console.log('\nTIPTAP-SICHER ' + res.filter(Boolean).length + '/' + res.length + ' OK – ' + (res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
