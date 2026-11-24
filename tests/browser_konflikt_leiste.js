// Konflikt-Leiste (Speichern -> 409) sichtbar und bedienbar (Bugs-DB 2256): sie lag unter der klebenden Kopfleiste
// (.page-bar), Text und Knoepfe waren verdeckt. Geprueft je Fall (Rechner 1440x900 / Handy 390x844, hell / dunkel) und
// Editor (Tiptap und Editor.js, Konto-Wahl; gibt der Server einen Editor fest vor, nur dieser): Leiste unter der Kopfzeile,
// Mittelpunkt von Text, "Neue Version laden" und "Meine Version behalten" trifft die Leiste (elementFromPoint) - oben und
// nach dem Scrollen; einmal echter Klick auf "Meine Version behalten" -> Rueckfrage.
// Lokal: HK_TEST_BASE=... CK=<Cookie> [SHOTS=<Ordner> SHOT=vorher|nachher] node tests/browser_konflikt_leiste.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
if (!BASE || !CK) { console.error('HK_TEST_BASE, CK setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const SHOTS = process.env.SHOTS, SHOT = process.env.SHOT || 'nachher';
async function until(f, ms = 10000) { const t0 = Date.now(); let v; while (Date.now() - t0 < ms) { v = await f().catch(() => null); if (v) return v; await sleep(150); } return v; }

// Leiste unter der Kopfzeile? Treffen die Mittelpunkte von Text und Knoepfen die Leiste (nicht die Kopfleiste)?
const check = (p) => p.evaluate(() => {
  const bar = document.getElementById('pg-conflict'); if (!bar) return null;
  const r = bar.getBoundingClientRect();
  const heads = [...document.querySelectorAll('.np-head, #page > .page-bar')].filter((h) => {
    const s = getComputedStyle(h); return (s.position === 'fixed' || s.position === 'sticky') && h.getClientRects().length; });
  const head = Math.max(0, ...heads.map((h) => h.getBoundingClientRect().bottom).filter((b) => b < innerHeight / 2));
  const pt = (e) => { if (!e) return { miss: 'fehlt' }; const q = e.getBoundingClientRect(), x = q.left + q.width / 2, y = q.top + q.height / 2;
    const h = document.elementFromPoint(x, y); return { in: !!h && bar.contains(h), y: Math.round(y), hit: h ? (h.id || h.className || h.tagName) : null }; };
  const btns = [...bar.querySelectorAll('button')];
  return { top: Math.round(r.top), head: Math.round(head), text: pt(bar.querySelector('.pgc-t')),
    load: pt(btns.find((b) => /Neue Version laden/.test(b.textContent))), keep: pt(btns.find((b) => /Meine Version behalten/.test(b.textContent))) };
});
const good = (m) => !!m && m.top >= m.head - 1 && m.text.in && m.load.in && m.keep.in;

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const CASES = [{ n: 'desktop-hell', vp: { width: 1440, height: 900 } }, { n: 'desktop-dunkel', vp: { width: 1440, height: 900 }, dark: true },
    { n: 'handy-hell', vp: { width: 390, height: 844 }, mobile: true }, { n: 'handy-dunkel', vp: { width: 390, height: 844 }, mobile: true, dark: true }];
  const blocks = Array.from({ length: 40 }, (_, i) => ({ id: 'k' + i, type: 'paragraph', data: { text: 'Absatz ' + i + ' mit etwas Text, damit die Seite scrollt.' } }));
  let clicked = false;
  for (const cs of CASES) {
    const ctx = await br.newContext({ viewport: cs.vp, locale: 'de-DE', colorScheme: cs.dark ? 'dark' : 'light', isMobile: !!cs.mobile, hasTouch: !!cs.mobile });
    await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); const errs = []; p.on('pageerror', (e) => errs.push(e.message));
    await p.goto(BASE + '/notes'); await sleep(300);
    const api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
    const was = await api('GET', '/api/account/editor').catch(() => ({}));
    const kinds = was.choice ? ['tiptap', 'editorjs'] : [was.engine || 'tiptap'];
    for (const kind of kinds) {
      const tag = `${cs.n} ${kind}`;
      if (was.choice) await api('PUT', '/api/account/editor', { tiptap: kind === 'tiptap' });
      const id = (await api('POST', '/api/notes', { title: 'Konflikt ' + kind, cover: null })).id;
      await api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks }), format: 'blocks' });
      await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
      const para = p.getByText('Absatz 3 mit etwas Text', { exact: false }).first();
      ok(`${tag}: Seite geladen`, await until(() => para.isVisible()));
      // Seit 2.5 fuehrt der Server gleichzeitige Aenderungen zusammen - hier zaehlt nur die Anzeige: Speichern -> 409
      let patched = 0;
      const route = (r) => { if (r.request().method() === 'PATCH') { patched++; return r.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: 'conflict', rev: 999, by: 'Bert' }) }); } return r.continue(); };
      await p.route('**/api/notes/' + id, route);
      await para.click(); await p.keyboard.press('End'); await p.keyboard.type(' neu', { delay: 30 });
      const shown = await until(() => p.evaluate(() => !!document.getElementById('pg-conflict')), 8000);
      await p.unroute('**/api/notes/' + id, route);
      ok(`${tag}: Speichern -> 409 -> Konflikt-Leiste`, shown, { patched });
      if (!shown) { await api('DELETE', '/api/notes/' + id); continue; }
      await p.evaluate(() => { const s = document.scrollingElement; s.scrollTop = 0; document.querySelectorAll('.np-main').forEach((m) => { m.scrollTop = 0; }); });
      await sleep(400);
      const m0 = await check(p);
      ok(`${tag}: oben - Leiste unter der Kopfzeile, Text und beide Knoepfe frei`, good(m0), m0);
      if (SHOTS) await p.screenshot({ path: `${SHOTS}/konflikt-${SHOT}-${cs.n}-${kind}.png` }).catch((e) => console.log('--   Bild: ' + e.message.split('\n')[0]));
      await p.mouse.move(cs.vp.width / 2, cs.vp.height / 2); await p.mouse.wheel(0, 900); await sleep(500);
      const m1 = await check(p);
      ok(`${tag}: gescrollt - Leiste klebt sichtbar unter der Kopfzeile, Text und Knoepfe frei`, good(m1) && m1.top < cs.vp.height / 3, m1);
      if (!clicked && !cs.mobile) {
        clicked = true;
        const k = p.locator('#pg-conflict button', { hasText: 'Meine Version behalten' });
        const bb = await k.boundingBox();
        if (bb) await p.mouse.click(bb.x + bb.width / 2, bb.y + bb.height / 2);
        ok(`${tag}: Klick auf "Meine Version behalten" -> Rueckfrage`, await until(() => p.evaluate(() => !!document.querySelector('.hk-ask')), 3000));
        await p.keyboard.press('Escape'); await sleep(200);
      }
      await api('DELETE', '/api/notes/' + id);
    }
    if (was.choice) await api('PUT', '/api/account/editor', { tiptap: was.engine === 'tiptap' });
    ok(`${cs.n}: keine Skriptfehler`, !errs.length, errs.slice(0, 3));
    await ctx.close();
  }
  await br.close();
  console.log(`\nBROWSER-KONFLIKT-LEISTE ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
