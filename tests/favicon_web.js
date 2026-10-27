// Favicons der Lesezeichen im Browser (2.5): Rueckfall auf icon_direct. Fake-Seiten auf 127.0.0.2 (der Test-Server
// laeuft mit SAGA_FAVICON_TEST_PUBLIC=127.0.0.2,127.0.0.3): dem Server (User-Agent "Saga-Favicon") antworten sie mit
// 503 (Bot-Schutz), dem Browser liefert Seite A /favicon.ico als Bild (/apple-touch-icon.png 404), Seite B nichts.
// Erwartung: Kachel A zeigt das direkt geladene Bild (erst icon_url 404, dann apple-touch-icon 404, dann favicon.ico),
// Kachel B den Buchstaben; Abrufe des Browsers ohne Referer und ohne Cookies; die CSP sperrt Bilder anderer Origins;
// dasselbe in Einstellungen -> Lesezeichen.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> node tests/favicon_web.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const http = require('http');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 8000)) return v; await sleep(150); } }
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAANElEQVR4nO3OMQEAMAjEwKdjRdS/FFwVGSwXA7m6/X4WO5tzAAAAAAAAAAAAAAAAAACAJBk9/wIv7EiHMQAAAABJRU5ErkJggg==', 'base64');
const PA = 18600 + Math.floor(Math.random() * 50), PB = PA + 50;
const hits = [];
function site(port, img) {
  const s = http.createServer((q, r) => {
    const srv = /Saga-Favicon/.test(q.headers['user-agent'] || '');
    hits.push({ port, path: q.url.split('?')[0], who: srv ? 'srv' : 'br', referer: q.headers.referer || '', cookie: q.headers.cookie || '' });
    if (srv) { r.writeHead(503, { 'Content-Length': 0 }); return r.end(); }
    if (img && q.url.split('?')[0] === '/favicon.ico') { r.writeHead(200, { 'Content-Type': 'image/png', 'Content-Length': PNG.length }); return r.end(PNG); }
    r.writeHead(404, { 'Content-Length': 0 }); r.end();
  });
  s.listen(port, '127.0.0.2'); return s;
}
(async () => {
  const sites = [site(PA, true), site(PB, false)];
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [], csp = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy/.test(m.text())) csp.push(m.text()); });
  await p.goto(BASE + '/');
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const A = await api('POST', '/api/bookmarks', { name: 'Alpha Web', url: `http://127.0.0.2:${PA}/start`, category: 'AAA Favtest' });
  const B = await api('POST', '/api/bookmarks', { name: 'Beta Web', url: `http://127.0.0.2:${PB}/`, category: 'AAA Favtest' });
  try {
    const list = await api('GET', '/api/bookmarks');
    const a = list.find((x) => x.id === A.id) || {};
    ok(`icon_direct fuer Lesezeichen A (${(a.icon_direct || []).join(', ')})`, (a.icon_direct || []).length === 2 && a.icon_url);
    await p.goto(BASE + '/?t=' + Date.now());
    const cspHdr = await p.evaluate(async () => (await fetch('/', { method: 'HEAD' })).headers.get('content-security-policy') || '');
    ok('CSP img-src enthaelt die Origins der Lesezeichen', cspHdr.indexOf(`http://127.0.0.2:${PA}`) >= 0 && cspHdr.indexOf(`http://127.0.0.2:${PB}`) >= 0, cspHdr);
    await p.evaluate(() => { const t = document.querySelector('.tw[data-bm]'); if (t) t.scrollIntoView(); });
    const tile = (id) => p.evaluate((id) => {
      const ic = document.querySelector(`.tw[data-bm="${id}"] .ic`); if (!ic) return null;
      const img = ic.querySelector('img');
      return { img: !!img, src: img ? img.getAttribute('src') : '', w: img ? img.naturalWidth : 0, txt: ic.textContent.trim(), cls: ic.className };
    }, id);
    const ta = await until(async () => { const t = await tile(A.id); return t && t.w === 32 ? t : null; });
    ok(`Kachel A: Bild direkt von der Seite (${ta && ta.src})`, ta && ta.src === `http://127.0.0.2:${PA}/favicon.ico` && /has-img/.test(ta.cls), await tile(A.id));
    const tb = await until(async () => { const t = await tile(B.id); return t && !t.img ? t : null; });
    ok(`Kachel B: nichts zu holen -> Buchstabe (${tb && tb.txt})`, tb && tb.txt === 'B' && !/has-img/.test(tb.cls), await tile(B.id));
    const brA = hits.filter((h) => h.port === PA && h.who === 'br').map((h) => h.path);
    ok(`Reihenfolge im Browser: apple-touch-icon, dann favicon.ico (${brA.join(', ')})`,
      brA.indexOf('/apple-touch-icon.png') >= 0 && brA.indexOf('/favicon.ico') > brA.indexOf('/apple-touch-icon.png'));
    ok('Server hat es zuerst versucht (503 Bot-Schutz)', hits.some((h) => h.port === PA && h.who === 'srv'));
    ok('Abrufe des Browsers ohne Referer und ohne Cookies', hits.filter((h) => h.who === 'br').every((h) => !h.referer && !h.cookie),
      hits.filter((h) => h.who === 'br'));
    // CSP: andere Origin (nicht unter den Lesezeichen) bleibt gesperrt
    const other = await p.evaluate((port) => new Promise((done) => {
      const i = new Image(); i.onload = () => done('geladen'); i.onerror = () => done('gesperrt');
      i.src = `http://127.0.0.3:${port}/favicon.ico`;
    }), PA);
    await sleep(300);
    ok(`CSP sperrt Bilder anderer Origins (${other}, ${csp.length} Meldung)`, other === 'gesperrt' && csp.some((m) => /127\.0\.0\.3/.test(m)));
    // Einstellungen -> Lesezeichen
    await p.evaluate(() => window.HKSettings.open('bookmarks'));
    const row = () => p.evaluate((name) => {
      const r = [...document.querySelectorAll('.hk-bm-row')].find((x) => x.textContent.indexOf(name) >= 0); if (!r) return null;
      const img = r.querySelector('.ic img');
      return { src: img ? img.getAttribute('src') : '', w: img ? img.naturalWidth : 0, rp: img ? img.referrerPolicy : '', txt: r.querySelector('.ic').textContent.trim() };
    }, 'Alpha Web');
    const ra = await until(async () => { const r = await row(); return r && r.w === 32 ? r : null; });
    ok(`Einstellungen: Bild direkt von der Seite, referrerPolicy no-referrer (${ra && ra.src})`,
      ra && ra.src === `http://127.0.0.2:${PA}/favicon.ico` && ra.rp === 'no-referrer', await row());
    ok('Keine Skriptfehler', !errs.length, errs);
  } finally {
    for (const x of [A, B]) if (x && x.id) await api('DELETE', '/api/bookmarks/' + x.id);
    await br.close(); sites.forEach((s) => s.close());
  }
  console.log(`\nFAVICON_WEB ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
