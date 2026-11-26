// Saga 2.6 (Bugs-DB 2541/2542) - Ort "Saga" auf breiten Fenstern und Quellenzeile unter Antworten:
// 2541: Spalte (Leiste B6, Verlauf, Eingabe) mittig im Platz neben der Seitenleiste (±2 px) bei 1280/1920/2560 px, mit
//       und ohne Seitenleiste; Leiste, Verlauf und Eingabe gleich breit und buendig; Ein-/Ausklappen gleitet (Zwischenwerte,
//       stetig, kein Sprung); schmal (390 px) volle Breite wie bisher. Gibt Zeichen je Zeile einer Antwort aus (Lesbarkeit).
// 2542: Antwort mit mehr als 3 Quellen: 3 Links + Knopf "{n} weitere" (aria-expanded/-controls, Name nennt "Quellen"),
//       Tastatur (Enter klappt auf, Fokus auf die erste weitere Quelle; Leertaste klappt zu, Fokus bleibt), nach dem Neuladen
//       (Verlauf) gleich; Englisch "{n} more".
// Richtet sich selbst ein: tests/kollegin_einwilligung.py --browser-setup/--browser-teardown, Fake-Modell tests/fakeai.py
// (Frage "What's coming up this week?" ueber 14 Seiten dieser Woche: "fake-demo" belegt alle 14 mit [n] -> 3 + "11 weitere";
// danach "fake-nichts" = Negativ-Antwort wie im Feld -> seit 2542 (Server) ganz ohne Quellenzeile).
// Env: SAGA_BASE, BASE_URL (Saga-Domain), HK_FAKEAI_PORT (Standard 8955), PY (python), SHOTS=/ordner (Bilder),
// PREFIX (Bildname-Praefix, Standard "nachher"; fuer den alten Stand PREFIX=vorher - die Pruefungen sind dann rot).
const PWL = require(process.env.PLAYWRIGHT || 'playwright');
const FF = process.env.BR === 'firefox', chromium = FF ? PWL.firefox : PWL.chromium;
const { execFileSync, spawn } = require('child_process');
const path = require('path');
const E = process.env; const BASE = E.SAGA_BASE || 'http://127.0.0.1:8950';
const PORT = E.HK_FAKEAI_PORT || '8955'; const PY = E.PY || 'python';
const PREFIX = E.PREFIX || 'nachher';
const HERE = __dirname;
const R = []; const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x === undefined ? '' : x).slice(0, 700))); };
const info = (n, x) => console.log('INFO ' + n + ' ' + JSON.stringify(x));
const errs = [];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const until = async (fn, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { const v = await fn(); if (v) return v; await sleep(150); } return null; };
const py = (...a) => JSON.parse(execFileSync(PY, [path.join(HERE, 'kollegin_einwilligung.py'), ...a], { env: E, encoding: 'utf8' }).trim().split('\n').pop());
const api = (p, m, u, b) => p.evaluate(([m, u, b]) => fetch(u, { method: m, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Saga': '1' },
  body: b ? JSON.stringify(b) : undefined }).then(r => r.text().then(t => { let d = null; try { d = JSON.parse(t); } catch (e) { d = t; } return { s: r.status, d }; })), [m, u, b]);

async function login(br, u, pw, opts) {
  const ctx = await br.newContext(Object.assign({ viewport: { width: 1920, height: 1080 }, locale: 'de-DE' }, opts || {}));
  if (opts && opts.theme) await ctx.addInitScript(t => { try { localStorage.setItem('hk-theme', t); } catch (e) { /* egal */ } }, opts.theme);
  const p = await ctx.newPage(); p.on('pageerror', e => { errs.push(e.message); console.log('[err]', e.message); });
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', u); await p.fill('input[name=password]', pw);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  return { ctx, p };
}
async function shot(p, name) {           // kopflos laeuft die Einblend-Animation (sg-in) erst mit dem naechsten Bild an
  if (E.SHOTS) { await p.waitForTimeout(300); await p.waitForFunction(() => document.getAnimations().every(a => a.playState !== 'running'), null, { timeout: 3000 }).catch(() => {});
    await p.screenshot({ path: E.SHOTS + '/' + PREFIX + '-' + name + (FF ? '-firefox' : '') + '.png' }); } }
async function openSaga(p) {
  await p.goto(BASE + '/saga');
  return until(() => p.evaluate(() => document.querySelectorAll('#sg-feed .sg-ai').length > 0 && !document.getElementById('sg-ask').hidden), 10000);
}
async function sideOff(p) {                         // Seitenleiste per Knopf aus und das Gleiten abwarten
  if (await p.evaluate(() => document.body.classList.contains('side-hidden'))) return;
  await p.click('#pg-side-btn'); await p.mouse.move(1200, 600); await p.waitForTimeout(450);
}

// Lage der Spalte: Mitte des Inhalts von main.sg gegen die Mitte des freien Platzes (rechts der Seitenleiste)
const MEASURE = () => {
  const m = document.querySelector('main.sg'), r = m.getBoundingClientRect(), cs = getComputedStyle(m);
  const docW = document.documentElement.clientWidth, hidden = document.body.classList.contains('side-hidden');
  const side = document.querySelector('.pages-side'), sr = side && side.getBoundingClientRect();
  const sideR = hidden || !sr || !sr.width || getComputedStyle(side).visibility === 'hidden' ? 0 : Math.max(0, sr.right);   // fest: offsetParent ist immer null
  const cl = r.left + parseFloat(cs.paddingLeft), cr = r.right - parseFloat(cs.paddingRight);
  const box = (id) => { const e = document.getElementById(id); if (!e || e.hidden || !e.offsetParent) return null; const b = e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.right)]; };
  return { docW, hidden, sideR: Math.round(sideR), left: Math.round(r.left), width: Math.round(r.width), colW: Math.round(cr - cl),
    off: Math.round(((cl + cr) / 2 - (sideR + docW) / 2) * 10) / 10, bar: box('sg-bar'),
    feed: box('sg-feed'), ask: box('sg-ask') };
};
const FOCUS_BTN = () => { const b = [].slice.call(document.querySelectorAll('#sg-feed .sg-srcs-b')).pop(); if (b) b.focus(); return !!b; };
// Kontrast (WCAG 2.x) der Knopfkante gegen die KI-Flaeche (2515: >= 3:1)
const EDGE = () => { const b = [].slice.call(document.querySelectorAll('#sg-feed .sg-srcs-b')).pop(); if (!b) return null;
  const rgb = (c) => String(c).match(/[\d.]+/g).map(Number).slice(0, 3);
  const lum = (c) => { const v = rgb(c).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  const e = getComputedStyle(b).borderTopColor, bg = getComputedStyle(b.closest('.sg-card')).backgroundColor, x = lum(e), y = lum(bg);
  return { e, bg, r: Math.round((Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05) * 100) / 100 }; };
const same = (a, b) => a && b && Math.abs(a[0] - b[0]) <= 1 && Math.abs(a[1] - b[1]) <= 1;

(async () => {
  const fk = spawn(PY, [path.join(HERE, 'fakeai.py'), PORT], { stdio: 'ignore' });
  await sleep(800);
  const cfg = py('--browser-setup');
  execFileSync(PY, ['-c', "import sys; sys.path.insert(0, sys.argv[1]); import kollegin_einwilligung as k; k.setai(wait=False, model='fake-demo')", HERE],
    { env: E, encoding: 'utf8' });
  await sleep(5600);                                 // Einstellungen im Server 5 s zwischengespeichert
  const br = await chromium.launch(!FF && E.PW_CHANNEL ? { channel: E.PW_CHANNEL } : {});
  try {
    let { ctx, p } = await login(br, cfg.a, cfg.pw, { theme: 'dark' });
    await api(p, 'PUT', '/api/account/lang', { lang: 'de' });
    await api(p, 'POST', '/api/first-steps', { answer: 'no' });
    const st = await api(p, 'GET', '/api/ai/status');
    if (st.d && st.d.intro && (!st.d.consent || st.d.consent.state !== 'granted'))
      await api(p, 'POST', '/api/ai/consent', { answer: 'no_ideas', version: st.d.intro.version, host: st.d.intro.host });
    // 14 Seiten, alle diese Woche bearbeitet: "this week" liest sie alle (saga/ai.py time_window) -> viele Quellen
    const TITLES = ['Elternabend Klasse 3b', 'Einkaufsliste', 'Gartenplanung Frühjahr', 'Fahrrad reparieren', 'Rezept Linsensuppe',
      'Urlaub Ostsee', 'Steuer 2026', 'Geschenkideen', 'Leseliste', 'Umzugskartons', 'Heizung ablesen', 'Vereinsfest Notizen',
      'Arzttermine Familie', 'Bücherei Rückgabe'];
    let made = 0;
    for (const [i, t] of TITLES.entries()) {
      const n = await api(p, 'POST', '/api/notes', { title: t });
      if (!n.d || !n.d.id) continue;
      const body = JSON.stringify({ blocks: [{ type: 'paragraph', data: { text: `Notiz ${i + 1} zu ${t}: diese Woche kurz angesehen, nichts Dringendes.` } }] });
      const u = await api(p, 'PATCH', '/api/notes/' + n.d.id, { body, format: 'blocks' });
      if (u.s === 200) made++;
    }
    ok('Vorbereitung: 14 Seiten mit Inhalt', made === TITLES.length, made);
    await p.goto(BASE + '/saga'); await until(() => p.evaluate(() => !document.getElementById('sg-ask').hidden), 8000);
    await p.fill('#sg-ta', "What's coming up this week?"); await p.press('#sg-ta', 'Enter');
    const ans = await until(() => p.evaluate(() => { const c = [].slice.call(document.querySelectorAll('#sg-feed .sg-ai')).pop();
      return c && !c.classList.contains('sg-live') ? (c.querySelector('.sg-answer') || {}).textContent : null; }), 25000);
    ok('Antwort mit Belegen (fake-demo)', /\[\d+\]|Das steht/.test(ans || ''), ans);
    const th = await api(p, 'GET', '/api/ai/thread');
    const last = ((th.d && th.d.turns) || []).filter(x => x.role === 'ai').pop() || {};
    const N = ((last.fields || {}).sources || []).length;
    info('2542 Server: Quellen am gespeicherten Zug (fields.sources)', N);
    ok('2542: Antwort belegt mehr als 3 Quellen', N > 3, N);

    // ---------- 2542: Quellenzeile ----------
    const SRC = (idx) => { const c = [].slice.call(document.querySelectorAll('#sg-feed .sg-ai'))[idx === undefined ? document.querySelectorAll('#sg-feed .sg-ai').length - 1 : idx];
      const w = c && c.querySelector('.sg-srcs'); if (!w) return null;
      const vis = [].slice.call(w.querySelectorAll('.sg-src')).filter(a => a.offsetParent !== null && a.getClientRects().length > 0);
      const b = w.querySelector('.sg-srcs-b'), ctl = b && document.getElementById(b.getAttribute('aria-controls') || '');
      const name = b ? (b.getAttribute('aria-label') || b.textContent) : null;
      return { all: w.querySelectorAll('.sg-src').length, vis: vis.length, btn: b ? b.textContent : null, name, exp: b && b.getAttribute('aria-expanded'),
        ctl: !!ctl, ctlHidden: ctl ? ctl.hidden : null, focus: document.activeElement && document.activeElement.className, focusTxt: document.activeElement && document.activeElement.textContent,
        tag: b && b.tagName, h: Math.round(w.getBoundingClientRect().height) };
    };
    let s0 = await p.evaluate(SRC);
    ok('2542: 3 Quellen sichtbar, Knopf „{n} weitere“ (n = Rest)', s0 && s0.vis === 3 && s0.all === N && s0.btn === `${N - 3} weitere`, s0);
    ok('2542: Knopf ist <button>, aria-expanded=false, aria-controls zeigt auf verborgene Liste, Name nennt „Quellen“ und den sichtbaren Text',
      s0 && s0.tag === 'BUTTON' && s0.exp === 'false' && s0.ctl && s0.ctlHidden === true && /Quellen/.test(s0.name || '') && (s0.name || '').indexOf(s0.btn) >= 0, s0);
    await shot(p, 'quellen-zu-1920-mit-dunkel');
    const ed = await p.evaluate(EDGE); ok('2542 dunkel: Kante des Knopfs >= 3:1 gegen die KI-Fläche', ed && ed.r >= 3, ed);
    await p.evaluate(FOCUS_BTN);
    if (s0 && s0.btn) await p.keyboard.press('Enter');
    await p.waitForTimeout(150);
    const s1 = await p.evaluate(SRC);
    ok('2542: Enter klappt auf: alle Quellen sichtbar, aria-expanded=true, Fokus auf der ersten weiteren Quelle',
      s1 && s1.vis === N && s1.exp === 'true' && s1.ctlHidden === false && s1.focus === 'sg-src' && s1.btn === 'weniger', s1);
    await shot(p, 'quellen-offen-1920-mit-dunkel');
    await p.evaluate(FOCUS_BTN);
    if (s1 && s1.btn) await p.keyboard.press('Space');
    await p.waitForTimeout(150);
    const s2 = await p.evaluate(SRC);
    ok('2542: Leertaste klappt zu, Fokus bleibt auf dem Knopf', s2 && s2.vis === 3 && s2.exp === 'false' && s2.focus === 'sg-srcs-b' && s2.btn === `${N - 3} weitere`, s2);
    // Negativ-Antwort wie im Feld (2542, Server): ganz ohne Quellenzeile
    execFileSync(PY, ['-c', "import sys; sys.path.insert(0, sys.argv[1]); import kollegin_einwilligung as k; k.setai(wait=True, model='fake-nichts')", HERE],
      { env: E, encoding: 'utf8' });
    await p.fill('#sg-ta', "What's coming up this week?"); await p.press('#sg-ta', 'Enter');
    const neg = await until(() => p.evaluate(() => { const cs = document.querySelectorAll('#sg-feed .sg-ai'), c = cs[cs.length - 1];
      return cs.length >= 2 && c && !c.classList.contains('sg-live') ? { t: (c.querySelector('.sg-answer') || {}).textContent, srcs: !!c.querySelector('.sg-srcs') } : null; }), 25000);
    const th2 = await api(p, 'GET', '/api/ai/thread');
    const last2 = ((th2.d && th2.d.turns) || []).filter(x => x.role === 'ai').pop() || {};
    ok('2542: „nichts gefunden“ ohne Quellenzeile (live) und ohne Quellen am gespeicherten Zug',
      neg && /nichts gefunden/.test(neg.t || '') && !neg.srcs && ((last2.fields || {}).sources || []).length === 0, { neg, f: last2.fields });
    await p.reload(); await openSaga(p);
    const nNeg = await p.evaluate(() => { const cs = document.querySelectorAll('#sg-feed .sg-ai'); return cs.length >= 2 && !cs[cs.length - 1].querySelector('.sg-srcs'); });
    ok('2542: nach dem Neuladen „nichts gefunden“ weiter ohne Quellenzeile', nNeg, nNeg);
    const s3 = await p.evaluate(SRC, 0);
    ok('2542: nach dem Neuladen (Verlauf) wieder 3 + „{n} weitere“', s3 && s3.vis === 3 && s3.btn === `${N - 3} weitere`, s3);
    await api(p, 'PUT', '/api/account/lang', { lang: 'en' });
    await p.reload(); await openSaga(p);
    const s4 = await p.evaluate(SRC, 0);
    ok('2542: Englisch „{n} more“, Name „Show {n} more sources“', s4 && s4.btn === `${N - 3} more` && /^Show \d+ more sources?$/.test(s4.name || ''), s4);
    await api(p, 'PUT', '/api/account/lang', { lang: 'de' });
    await p.reload(); await openSaga(p);

    // ---------- 2541: Lage der Spalte ----------
    const cpl = await p.evaluate(() => { const a = document.querySelector('#sg-feed .sg-ai .sg-answer'); if (!a) return null;
      const s = document.createElement('span'); s.style.whiteSpace = 'nowrap';
      s.textContent = 'Am Donnerstag ist der Elternabend in der Schule, danach geht es zum Einkaufen und am Wochenende in den Garten. '.repeat(3);
      a.appendChild(s); const cw = s.getBoundingClientRect().width / s.textContent.length; s.remove();
      return { answerW: Math.round(a.clientWidth), charW: Math.round(cw * 100) / 100, cpl: Math.round(a.clientWidth / cw) }; });
    info('2541 Lesbarkeit: Zeichen je Zeile in einer Antwort (1920 px)', cpl);
    await ctx.close();
    for (const [w, h] of [[1280, 900], [1920, 1080], [2560, 1440]]) {
      ({ ctx, p } = await login(br, cfg.a, cfg.pw, { viewport: { width: w, height: h }, theme: 'dark' }));
      await openSaga(p); await p.mouse.move(w - 50, h / 2); await p.waitForTimeout(300);
      const a = await p.evaluate(MEASURE);
      info(`2541 ${w} mit Leiste`, a);
      ok(`2541 ${w} px mit Seitenleiste: Spalte mittig im freien Platz (±2 px)`, a && !a.hidden && a.sideR > 200 && Math.abs(a.off) <= 2 && a.left >= a.sideR, a);
      ok(`2541 ${w} px mit Seitenleiste: Leiste B6, Verlauf und Eingabe gleich breit und bündig`, a && same(a.feed, a.ask) && (!a.bar || same(a.bar, a.feed)), a);
      // Ein-/Ausklappen: linke Kante je Bild abtasten
      const xs = await p.evaluate(() => new Promise(res => { const m = document.querySelector('main.sg'), xs = [], t0 = performance.now();
        document.getElementById('pg-side-btn').click();
        (function f() { xs.push(Math.round(m.getBoundingClientRect().left * 10) / 10); if (performance.now() - t0 < 500) requestAnimationFrame(f); else res(xs); })(); }));
      const mids = new Set(xs.filter(x => x !== xs[0] && x !== xs[xs.length - 1])).size;
      const mono = xs.every((x, i) => i === 0 || x <= xs[i - 1] + 0.5);
      const jump = Math.max(0, ...xs.map((x, i) => i ? Math.abs(x - xs[i - 1]) : 0));
      info(`2541 ${w} Ausklappen (linke Kante je Bild)`, { n: xs.length, first: xs[0], last: xs[xs.length - 1], mids, maxStep: jump });
      ok(`2541 ${w} px: Ausblenden der Leiste gleitet (Zwischenwerte, stetig)`, mids >= 3 && mono, { mids, mono, xs: xs.slice(0, 40) });
      await p.mouse.move(w - 50, h / 2); await p.waitForTimeout(400);
      const b = await p.evaluate(MEASURE);
      info(`2541 ${w} ohne Leiste`, b);
      ok(`2541 ${w} px ohne Seitenleiste: Spalte mittig im Fenster (±2 px)`, b && b.hidden && Math.abs(b.off) <= 2, b);
      ok(`2541 ${w} px ohne Seitenleiste: Leiste B6, Verlauf und Eingabe gleich breit und bündig`, b && same(b.feed, b.ask) && (!b.bar || same(b.bar, b.feed)), b);
      ok(`2541 ${w} px: Spaltenbreite mit und ohne Leiste gleich`, a && b && Math.abs(a.colW - b.colW) <= 1, { a: a && a.colW, b: b && b.colW });
      await p.click('#pg-side-btn'); await p.mouse.move(w - 50, h / 2); await p.waitForTimeout(450);
      const c = await p.evaluate(MEASURE);
      ok(`2541 ${w} px: wieder eingeblendet -> wie vorher`, c && !c.hidden && Math.abs(c.left - a.left) <= 1, { a: a && a.left, c: c && c.left });
      await ctx.close();
    }
    ({ ctx, p } = await login(br, cfg.a, cfg.pw, { viewport: { width: 390, height: 844 }, theme: 'dark', isMobile: true, hasTouch: true }));
    await openSaga(p);
    const mb = await p.evaluate(() => { const m = document.querySelector('main.sg'), r = m.getBoundingClientRect(), cs = getComputedStyle(m);
      return { left: Math.round(r.left), width: Math.round(r.width), docW: document.documentElement.clientWidth, pl: cs.paddingLeft, pr: cs.paddingRight, ml: cs.marginLeft }; });
    info('2541 390 px', mb);
    ok('2541 390 px: volle Breite, Innenabstand wie bisher (.9rem = 14,4 px)', mb.left === 0 && mb.width === mb.docW && mb.ml === '0px' && mb.pl === '14.4px' && mb.pr === '14.4px', mb);
    const sm = await p.evaluate(SRC, 0);
    ok('2542 390 px: 3 Quellen + „{n} weitere“', sm && sm.vis === 3 && sm.btn === `${N - 3} weitere`, sm);
    await ctx.close();

    // ---------- Bilder ----------
    if (E.SHOTS) {
      for (const thm of ['dark', 'light']) {
        const tn = thm === 'dark' ? 'dunkel' : 'hell';
        for (const [w, h] of [[1920, 1080], [2560, 1440]]) for (const side of ['mit', 'ohne']) {
          const L = await login(br, cfg.a, cfg.pw, { viewport: { width: w, height: h }, theme: thm });
          await openSaga(L.p); if (side === 'ohne') await sideOff(L.p); else { await L.p.mouse.move(w - 50, h / 2); await L.p.waitForTimeout(300); }
          await shot(L.p, `saga-${w}-${side}-leiste-${tn}`); await L.ctx.close();
        }
        const L = await login(br, cfg.a, cfg.pw, { viewport: { width: 390, height: 844 }, theme: thm, isMobile: true, hasTouch: true });
        await openSaga(L.p); await shot(L.p, `saga-390-${tn}`); await L.ctx.close();
        if (thm === 'light') {
          const Q = await login(br, cfg.a, cfg.pw, { viewport: { width: 1920, height: 1080 }, theme: thm });
          await openSaga(Q.p); await Q.p.evaluate(() => { const b = [].slice.call(document.querySelectorAll('#sg-feed .sg-srcs-b')).pop(); if (b) b.click(); });
          const eh = await Q.p.evaluate(EDGE); ok('2542 hell: Kante des Knopfs >= 3:1 gegen die KI-Fläche', eh && eh.r >= 3, eh);
          await shot(Q.p, 'quellen-offen-1920-mit-hell'); await Q.ctx.close();
        }
      }
    }
    ok('keine Skriptfehler', errs.length === 0, errs);
  } catch (e) {
    ok('Ausnahme', false, String(e && e.stack || e));
  } finally {
    await br.close().catch(() => {});
    try { py('--browser-teardown'); } catch (e) { console.log('[teardown]', String(e).slice(0, 300)); }
    fk.kill();
  }
  const bad = R.filter(r => !r[1]).length;
  console.log(`== ${R.length - bad}/${R.length} OK`);
  process.exit(bad ? 1 : 0);
})();
