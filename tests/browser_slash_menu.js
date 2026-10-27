// Block-Menue "/" (2.5, Fehlerbericht): Menue liegt ganz im Fenster (nicht links abgeschnitten, "…nts gefunden") und
// deckt die Bloecke darunter ab - auch innere Editoren (Spalten), Kalender-Umschalter, Kommentar-Knopf und Spaltengriff.
// Seite wie im Bericht: Schaltflaechen, leerer Absatz, Kalender-Block (Liste, ohne CalDAV) - einmal neben-, einmal untereinander.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Sitzungs-Cookie hk-test-a> node tests/browser_slash_menu.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const BTN = { type: 'buttons', data: { buttons: [{ label: 'Neu', icon: '✅' }, { label: 'Journal' }, { label: 'Notiz' }] } };
const CAL = { type: 'calendar', data: { view: 'list', days: 14 } };
const P = { type: 'paragraph', data: { text: '' } };
const LAYOUTS = {
  nebeneinander: [BTN, { type: 'columns', data: { cols: [{ blocks: [P] }, { blocks: [CAL] }] } }],
  darunter: [BTN, P, { type: 'columns', data: { cols: [{ blocks: [CAL] }, { blocks: [{ type: 'paragraph', data: { text: 'rechts' } }] }] } }],
  spalte: [BTN, { type: 'columns', data: { cols: [{ blocks: [P, CAL] }, { blocks: [{ type: 'paragraph', data: { text: 'rechts' } }] }] } }],
  einfach: [BTN, P, CAL],
};
// Menue-Rechteck (ohne Einblend-Animation) und was an Rasterpunkten darin zuoberst liegt
const MEASURE = () => {
  const pop = [...document.querySelectorAll('#pg-editor .ce-popover--opened')].filter((x) => !x.closest('.ce-settings')).pop();
  if (!pop) return null;
  const c = pop.querySelector(':scope > .ce-popover__container'); c.style.animation = 'none';
  const r = c.getBoundingClientRect(), vw = document.documentElement.clientWidth, vh = innerHeight, fremd = {};
  let n = 0;
  for (let x = r.left + 3; x < r.right - 3; x += 10) for (let y = r.top + 3; y < r.bottom - 3; y += 10) {
    if (x < 0 || y < 0 || x >= vw || y >= vh) continue;
    n++; const e = document.elementFromPoint(x, y);
    if (!e || !pop.contains(e)) { const k = e ? String(e.className && e.className.baseVal !== undefined ? e.tagName : e.className || e.tagName).slice(0, 30) : 'nichts'; fremd[k] = (fremd[k] || 0) + 1; }
  }
  return { l: Math.round(r.left), r: Math.round(r.right), t: Math.round(r.top), b: Math.round(r.bottom), vw, vh, n, fremd,
    bg: getComputedStyle(c).backgroundColor, fixed: getComputedStyle(c).position === 'fixed',
    nf: !!pop.querySelector('.ce-popover__nothing-found-message--displayed') };
};
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  let errs = [];
  const CASES = [
    ['nebeneinander', 740, 'dark', 0], ['nebeneinander', 700, 'dark', 0], ['nebeneinander', 1280, 'dark', 0],
    ['darunter', 1280, 'dark', 0], ['darunter', 740, 'light', 0], ['darunter', 1920, 'dark', 1],
    ['spalte', 1440, 'dark', 0], ['spalte', 800, 'dark', 0], ['einfach', 1280, 'light', 0], ['einfach', 390, 'dark', 0]];
  for (const [lay, w, scheme, wide] of CASES) {
    const ctx = await br.newContext({ viewport: { width: w, height: 900 }, locale: 'de-DE', colorScheme: scheme });
    await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage();
    p.on('pageerror', (e) => errs.push(e.message));
    await p.goto(BASE + '/notes');
    const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
      const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
      return r.json().catch(() => ({}));
    }, [m, url, b]);
    const id = (await api('POST', '/api/notes', { title: 'Slash ' + lay })).id;
    await api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks: LAYOUTS[lay] }), format: 'blocks' });
    if (wide) await api('PATCH', '/api/notes/' + id, { wide: true });
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
    await p.waitForSelector('.embed-cal .ag-head'); await p.waitForTimeout(700);
    const tag = `${lay} ${w}px ${scheme}${wide ? ' breit' : ''}`;
    const para = p.locator('#pg-editor .ce-paragraph').first();
    await para.click(); await p.keyboard.type('/'); await p.waitForTimeout(600);
    // Maus ueber den Kalender: dessen Umschalter (Monat/Liste) erscheint und darf nicht ueber dem Menue liegen
    const hb = await p.locator('.embed-cal .ag-head').first().boundingBox();
    if (hb && w > 650) { await p.mouse.move(hb.x + 20, hb.y + 6); await p.waitForTimeout(200); }
    let m = await p.evaluate(MEASURE);
    ok(`${tag}: Menue offen`, !!m, m);
    if (!m) { await api('DELETE', '/api/notes/' + id); await ctx.close(); continue; }
    ok(`${tag}: Menue ganz im Fenster (links ${m.l}, rechts ${m.r} von ${m.vw})`, m.l >= 0 && m.r <= m.vw && m.t >= 0 && m.b <= m.vh, m);
    ok(`${tag}: Menue deckend und zuoberst`, Object.keys(m.fremd).length === 0 && m.n > 20 && !/rgba\(.*, 0\)|transparent/.test(m.bg), m);
    if (w > 650) {                                   // "Nichts gefunden" vollstaendig lesbar
      await p.keyboard.type('/'); await p.waitForTimeout(400);
      m = await p.evaluate(MEASURE);
      ok(`${tag}: "Nichts gefunden" sichtbar und im Fenster`, m && m.nf && m.l >= 0 && Object.keys(m.fremd).length === 0, m);
    } else ok(`${tag}: Handy - Menue als Leiste unten ueber die volle Breite`, m.fixed && m.r - m.l > w * 0.9, m);
    await p.keyboard.press('Escape'); await p.waitForTimeout(250);
    await api('DELETE', '/api/notes/' + id);
    await ctx.close();
  }
  ok('Keine Seitenfehler', !errs.length, errs);
  await br.close();
  console.log('Slash-Menue ' + res.filter(Boolean).length + '/' + res.length + (res.every(Boolean) ? ' ALLES GRUEN' : ''));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
