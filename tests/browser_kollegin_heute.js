// Saga 2.6 (Bugs-DB 2271 K-D) - Tageszeile "Kurz gesagt" auf "Heute" (static/heute-kurz.js, Vertrag docs/APP-API-KI.md §13):
// - Vertrag: jede Antwort aus tests/today_vectors.json (per Route vorgetaeuscht) zeigt genau 'show' (Teile, a11y, Pille, Frage,
//   "Wartet auf dich"); 404/403/enabled false -> nichts; unbekanntes Ziel -> Teil kein Knopf.
// - Echter Server (Konto kdw-an): unter der Lagezeile, keine KI-Marke, ohne aria-live, nie Fokus; Tipp oeffnet nur (hakt nichts
//   ab); XSS im Titel bleibt Text; ✕ -> Pille gleicher Hoehe, auch nach Neuladen und in einem zweiten Kontext; "Wieder an";
//   3x ✕ -> genau einmal die Frage; ⋯ "Warum sehe ich das?" / "Tageszeile ausblenden"; Einstellungen › Saga Schalter.
// - Sie-Konto (kdw-sie): "… warten auf Sie"; Konto ohne ai (kdw-aus): kein Element, Lagezeile unveraendert.
// - Ruhiger Tag (Vektor): kein Element; Layout-Shift < 0,05 (nur Chromium); Live ai.today.changed entprellt (400 ms) und aus
//   einem zweiten Tab (sobald der Server es sendet - sonst OFFEN); 390 px, 200 % Zoom, hell/dunkel (Kontrast), Thai >= 1,6.
// Richtet sich selbst ein: tests/kollegin_heute_setup.py (--setup/--teardown). Env: SAGA_BASE, PY (python), BR=firefox,
// SHOTS=/ordner (Bilder), NUR_BILDER=vorher (nur "vorher": heute-kurz.js blockiert), KEEP=1 (Konten stehen lassen).
const PWL = require(process.env.PLAYWRIGHT || 'playwright');
const FF = process.env.BR === 'firefox', engine = FF ? PWL.firefox : PWL.chromium;
const { execFileSync } = require('child_process');
const path = require('path'), fs = require('fs');
const E = process.env, BASE = E.SAGA_BASE || 'http://127.0.0.1:8940', PY = E.PY || 'python', HERE = __dirname;
const V = JSON.parse(fs.readFileSync(path.join(HERE, 'today_vectors.json'), 'utf8'));
const R = [], OFFEN = [];
const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x === undefined ? '' : x).slice(0, 700))); };
const offen = (n, x) => { OFFEN.push(n); console.log('OFFEN ' + n + (x ? ' ' + JSON.stringify(x).slice(0, 300) : '')); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (fn, ms) => { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(120); } };
const py = (...a) => JSON.parse(execFileSync(PY, [path.join(HERE, 'kollegin_heute_setup.py'), ...a], { env: E, encoding: 'utf8' }).trim().split('\n').pop());
const errs = [];
const CONTRAST = `(() => { const rgb = (c) => { const m = String(c).match(/[\\d.]+/g).map(Number); return /^color\\(/.test(c) ? m.slice(0, 3).map((v) => v * 255) : m.slice(0, 3); };
  const lum = (c) => { const v = rgb(c).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  return (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }; })()`;

async function login(br, u, pw, opts) {
  opts = opts || {};
  const ctx = await br.newContext({ viewport: opts.viewport || { width: 1440, height: 900 }, deviceScaleFactor: opts.dsf || 1,
    locale: 'de-DE', timezoneId: 'Europe/Berlin' });
  if (opts.theme) await ctx.addInitScript((t) => { try { localStorage.setItem('hk-theme', t); } catch (e) { /* egal */ } }, opts.theme);
  if (opts.lang) await ctx.addCookies([{ name: 'saga_lang', value: opts.lang, url: BASE }]);
  await ctx.addInitScript(() => {                 // Layout-Shift (Chromium) mitschreiben
    window.__cls = 0; window.__ls = [];
    try {
      new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) { window.__cls += e.value; window.__ls.push([+e.value.toFixed(4), Math.round(e.startTime), (e.sources || []).map((x) => x.node ? (x.node.id || x.node.className || x.node.nodeName) : '?').join(',').slice(0, 80)]); } })
        .observe({ type: 'layout-shift', buffered: true });
    } catch (e) { window.__cls = null; }
  });
  const p = await ctx.newPage(); p.on('pageerror', (e) => { errs.push(e.message); console.log('[err]', e.message); });
  if (opts.block) await p.route('**/static/heute-kurz.js*', (r) => r.fulfill({ contentType: 'application/javascript', body: '/* vorher */' }));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', u); await p.fill('input[name=password]', pw);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  return { ctx, p };
}
const api = (p, m, u, b) => p.evaluate(([m, u, b]) => fetch(u, { method: m, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Saga': '1' },
  body: b ? JSON.stringify(b) : undefined }).then((r) => r.text().then((t) => { let d = null; try { d = JSON.parse(t); } catch (e) { d = t; } return { s: r.status, d }; })), [m, u, b]);
async function shot(p, name) {
  if (!E.SHOTS) return;
  await p.evaluate(() => { const a = document.activeElement; if (a && a.blur) a.blur(); });
  await p.waitForTimeout(300); await p.screenshot({ path: path.join(E.SHOTS, name + (FF ? '-firefox' : '') + '.png') });
}
async function heute(p) { await p.goto(BASE + '/dashboard'); await p.waitForLoadState('load'); await settle(p); }
async function settle(p) { await p.waitForTimeout(250); await until(() => p.evaluate(() => { const b = document.getElementById('hz-kurz-box'); return !b || b.style.minHeight === ''; }), 4000); await p.waitForTimeout(150); }
function read(p) {
  return p.evaluate(() => {
    const b = document.getElementById('hz-kurz-box'); if (!b) return { absent: true, vis: false };
    const row = b.querySelector('#hz-kurz .hz-kurz-row:not(.is-quiet):not(.is-ask)'), q = b.querySelector('#hz-kurz .is-quiet'), a = b.querySelector('#hz-kurz .is-ask');
    const w = b.querySelector('#hz-wait .hz-wait-a');
    return {
      vis: !b.hidden && b.getClientRects().length > 0,
      kurz: row ? { label: row.querySelector('.hz-kurz-l').textContent, parts: [...row.querySelectorAll('.hz-kurz-p')].map((x) => x.textContent),
        a11y: row.getAttribute('aria-label'), tags: [...row.querySelectorAll('.hz-kurz-p')].map((x) => x.tagName) } : null,
      pill: q ? { text: q.querySelector('.hz-kurz-pt').textContent, undo: q.querySelector('.hz-kurz-undo').textContent } : null,
      ask: a ? { text: a.querySelector('.hz-kurz-q').textContent, yes: a.querySelector('.hz-kurz-yes').textContent, no: a.querySelector('.hz-kurz-no').textContent } : null,
      waiting: w ? w.textContent : null, href: w ? w.getAttribute('href') : null,
      text: b.textContent, imgs: b.querySelectorAll('img, svg').length,
      live: !!(b.querySelector('[aria-live]') || b.closest('[aria-live]')), focusIn: b.contains(document.activeElement),
      h: Math.round(b.getBoundingClientRect().height), rowH: Math.round(document.getElementById('hz-kurz').getBoundingClientRect().height)
    };
  });
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function showOf(s) { return { kurz: s.kurz ? { label: s.kurz.label, parts: s.kurz.parts, a11y: s.kurz.a11y } : null, pill: s.pill, ask: s.ask, waiting: s.waiting }; }
function seen(r) { return { kurz: r.kurz ? { label: r.kurz.label, parts: r.kurz.parts, a11y: r.kurz.a11y } : null, pill: r.pill, ask: r.ask, waiting: r.waiting }; }

async function mock(p, status, body, delay) {
  await p.unroute('**/api/ai/today**').catch(() => {});
  await p.route('**/api/ai/today**', async (r) => {
    if (delay) await sleep(delay);
    if (status === 404) return r.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"not found"}' });
    return r.fulfill({ status, contentType: 'application/json', headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify(body || {}) });
  });
}
const caseOf = (id) => V.cases.find((c) => c.id === id);

async function vorher(br, cfg) {
  for (const [name, o] of [['heute-desktop-hell', { theme: 'light' }], ['heute-desktop-dunkel', { theme: 'dark' }], ['heute-mobil-hell', { theme: 'light', viewport: { width: 390, height: 844 } }]]) {
    const { ctx, p } = await login(br, cfg.an, cfg.pw, Object.assign({ block: true }, o));
    await heute(p); await p.waitForTimeout(800); await shot(p, 'vorher-' + name); await ctx.close();
  }
}

(async () => {
  const cfg = py('--setup');
  await sleep(5600);                                  // Einstellungen im Server 5 s zwischengespeichert
  const br = await engine.launch();
  try {
    if (E.NUR_BILDER === 'vorher') { await vorher(br, cfg); return; }

    // ================= 1. Vertrag: Vektoren (vorgetaeuscht) =================
    {
      const { ctx, p } = await login(br, cfg.an, cfg.pw);
      for (const c of V.cases) {
        if (!/^\/api\/ai\/today/.test(c.request.path) || c.status === 304) continue;
        await mock(p, c.status, c.response); await heute(p);
        const r = await read(p), want = showOf(c.show), got = seen(r);
        const none = !c.show.kurz && !c.show.pill && !c.show.ask && !c.show.waiting;
        ok(`Vektor ${c.id}: Anzeige = show`, same(want, got) && (none ? !r.vis : r.vis), { want, got, vis: r.vis });
        if (c.show.kurz) ok(`Vektor ${c.id}: Teile sind Knoepfe, keine Bilder/KI-Marke, ohne aria-live, kein Fokus`,
          r.kurz.tags.every((t) => t === 'BUTTON') && !r.imgs && !/✦|\bKI\b|\bAI\b/.test(r.text) && !r.live && !r.focusIn, r);
      }
      // unbekanntes Ziel -> Teil nicht antippbar; Ziele: Termine -> /notes?v=events (Kalender rechts nicht verbunden)
      const n = JSON.parse(JSON.stringify(caseOf('normal').response));
      n.line.parts[2].target = { type: 'weltraum', id: 1 }; n.v = 'aaaaaaaaaaaa';
      await mock(p, 200, n); await heute(p);
      let r = await read(p);
      ok('unbekannter target.type -> Teil ohne Knopf', same(r.kurz && r.kurz.tags, ['BUTTON', 'BUTTON', 'SPAN']), r.kurz);
      ok('"Wartet auf dich" -> Saga › Auftraege', r.href === '/saga?tab=jobs', r.href);
      const calOn = await p.evaluate(() => { const c = document.getElementById('rail-cal'); return !!c && !c.hidden; });
      if (!calOn) {
        await Promise.all([p.waitForURL(/\/notes\?v=events/, { timeout: 6000 }).catch(() => null), p.click('#hz-kurz .hz-kurz-p >> nth=0')]);
        ok('Termin-Teil -> /notes?v=events (ohne Kalender rechts)', /\/notes\?v=events/.test(p.url()), p.url());
      }
      // Live: ai.today.changed entprellt (400 ms) - drei Ereignisse -> eine Anfrage; gleiches v -> keine
      await mock(p, 200, caseOf('normal').response); await heute(p);
      const hLine = (await read(p)).rowH;
      let gets = 0; p.on('request', (q) => { if (/\/api\/ai\/today(\?|$)/.test(q.url()) && q.method() === 'GET') gets++; });
      const fire = (v) => p.evaluate((v) => { for (let i = 0; i < 3; i++) document.dispatchEvent(new CustomEvent('hk-live', { detail: { type: 'ai.today.changed', data: { v } } })); }, v);
      await fire(caseOf('normal').response.v); await p.waitForTimeout(700);
      ok('Live: gleiches v -> kein Neuladen', gets === 0, gets);
      await mock(p, 200, caseOf('ruhe').response);
      await fire('ffffffffffff'); await p.waitForTimeout(200);
      const early = gets; await p.waitForTimeout(600);
      r = await read(p);
      ok('Live: drei Ereignisse -> genau eine Anfrage nach 400 ms, Anzeige neu (Pille)', early === 0 && gets === 1 && !!r.pill, { early, gets, pill: r.pill });
      ok('Pille so hoch wie eine einzeilige Zeile (±2 px)', Math.abs(r.rowH - hLine) <= 2, { hLine, h: r.rowH });
      await mock(p, 200, caseOf('frage').response); await heute(p);
      const rq = await read(p);
      ok('Frage so hoch wie eine einzeilige Zeile (±2 px)', !!rq.ask && Math.abs(rq.rowH - hLine) <= 2, { hLine, h: rq.rowH });
      // Layout-Shift (Chromium): frischer Kontext, Antwort kommt spaet (nach dem ersten Bild), danach Neuladen mit gemerkter Hoehe
      if (!FF) {
        await ctx.close();
        const base = await login(br, cfg.an, cfg.pw, { block: true }); await heute(base.p); await base.p.waitForTimeout(1200);
        const cls0 = await base.p.evaluate(() => window.__cls); console.log('     Grundverschiebung ohne Zeile', JSON.stringify(await base.p.evaluate(() => window.__ls))); await base.ctx.close();
        const a = await login(br, cfg.an, cfg.pw); await mock(a.p, 200, caseOf('normal').response, 350);
        await heute(a.p); await a.p.waitForTimeout(1200);
        const cls1 = await a.p.evaluate(() => [window.__cls, window.__ls]);
        await heute(a.p); await a.p.waitForTimeout(1200);
        const cls2 = await a.p.evaluate(() => [window.__cls, window.__ls]);
        console.log('     CLS ohne Zeile', cls0, '| erstes Laden (Antwort +350 ms)', JSON.stringify(cls1), '| Neuladen', JSON.stringify(cls2));
        ok('Layout-Shift durch die Zeile < 0,05 (erstes Laden, spaete Antwort)', cls1[0] - cls0 < 0.05, { cls0, cls1 });
        ok('Layout-Shift durch die Zeile beim Neuladen (Hoehe vorgemerkt) < 0,01', cls2[0] - cls0 < 0.01, { cls0, cls2 });
        await a.ctx.close();
      } else await ctx.close();
    }

    // ================= 2. Echter Server: kdw-an =================
    const A = await login(br, cfg.an, cfg.pw, { theme: 'light' });
    let p = A.p;
    await heute(p);
    let r = await read(p);
    const pos = await p.evaluate(() => {
      const l = document.getElementById('hz-lage').getBoundingClientRect(), b = document.getElementById('hz-kurz-box').getBoundingClientRect(),
        a = document.querySelector('.hz-acts').getBoundingClientRect();
      return { below: b.top >= l.bottom - 1, aboveActs: b.bottom <= a.top + 1, sib: document.getElementById('hz-lage').nextElementSibling.id,
        lage: document.getElementById('hz-lage').textContent, xss: window.__kdxss || null, imgs: document.querySelectorAll('#hz-kurz-box img').length };
    });
    ok('Zeile direkt unter der Lagezeile (vor den Schnellaktionen)', r.vis && pos.below && pos.aboveActs && pos.sib === 'hz-kurz-box', { pos, r });
    ok('Teile vom Server: ueberfaellig + heute faellig, Label "Kurz gesagt"', !!r.kurz && r.kurz.label === 'Kurz gesagt' && r.kurz.parts.length >= 2 && /überfällig/.test(r.kurz.parts.join(' ')), r.kurz);
    ok('XSS im Titel bleibt Text (kein <img>, kein Skript)', !pos.xss && !pos.imgs && /<img src=x/.test(r.text), { pos, t: r.text.slice(0, 120) });
    ok('keine KI-Marke, ohne aria-live, kein Fokus beim Laden', !/✦|\bKI\b/.test(r.text) && !r.live && !r.focusIn, r);
    ok('Lagezeile bleibt Zahlen (kein Titel aus der Zeile)', !/Kuchen|Pavillon/.test(pos.lage) && /überfällig/.test(pos.lage), pos.lage);
    ok('"Wartet auf dich" mit Auftrag', /wartet auf dich/.test(r.waiting || ''), r.waiting);
    await shot(p, 'nachher-heute-desktop-hell');
    // Trefferflaechen Desktop >= 32 px
    const hit = await p.evaluate(() => [...document.querySelectorAll('#hz-kurz-box button, #hz-kurz-box a')].map((b) => Math.round(Math.min(b.getBoundingClientRect().height, b.getBoundingClientRect().width))));
    const glue = await p.evaluate(() => { const ps = [...document.querySelectorAll('#hz-kurz .hz-kurz-p')], x = document.querySelector('#hz-kurz .hz-kurz-x');
      const a = ps[ps.length - 1].getBoundingClientRect(), b = x.getBoundingClientRect(); return Math.abs((a.top + a.bottom) / 2 - (b.top + b.bottom) / 2) < 8; });
    ok('✕/⋯ stehen in der Zeile des letzten Teils', glue);
    ok('Knoepfe >= 32 px (Desktop)', hit.length && hit.every((h) => h >= 32), hit);
    // Kontrast hell
    const con = async () => p.evaluate((C) => { const f = eval(C), bg = getComputedStyle(document.body).backgroundColor;
      const c = (s) => { const e = document.querySelector(s); return e ? +f(getComputedStyle(e).color, bg).toFixed(2) : null; };
      return { p: c('.hz-kurz-p'), l: c('.hz-kurz-l'), w: c('.hz-wait-a'), x: c('.hz-kurz-x') }; }, CONTRAST);
    let k = await con();
    ok('Kontrast hell >= 4,5 (Teil, Label, Wartet; ✕ >= 3)', k.p >= 4.5 && k.l >= 4.5 && k.w >= 4.5 && k.x >= 3, k);

    // Tipp oeffnet nur, hakt nichts ab
    const overBtn = await p.$('#hz-kurz .hz-kurz-p[data-kind=over]');
    await overBtn.click(); await p.waitForTimeout(500);
    const fl = await p.evaluate(() => { const n = document.querySelector('.hz-flash'); return n ? (n.dataset.key || n.id) : null; });
    const tl = await api(p, 'GET', '/api/tasks');
    const openIds = JSON.stringify(tl.d).match(/"id":\s*\d+/g) || [];
    const still = (tl.d.open || tl.d.items || []).some((t) => t.id === cfg.ids.over && !t.done);
    ok('Tipp auf "ueberfaellig" hebt die Aufgabe hervor und hakt nichts ab', fl === 't:' + cfg.ids.over && still, { fl, still, n: openIds.length });

    // ✕ -> Pille gleicher Hoehe; Neuladen; zweiter Kontext
    await p.click('#hz-kurz .hz-kurz-x'); await until(async () => !!(await read(p)).pill, 4000);
    r = await read(p);
    ok('✕ -> Pille "Ruhe bis morgen · Wieder an", Zeile weg, "Wartet" bleibt', !!r.pill && !r.kurz && r.pill.text === 'Ruhe bis morgen' && r.pill.undo === 'Wieder an' && !!r.waiting, r);
    ok('Fokus nach ✕ auf "Wieder an"', await p.evaluate(() => document.activeElement && document.activeElement.classList.contains('hz-kurz-undo')));
    await shot(p, 'nachher-pille-desktop-hell');
    await heute(p); r = await read(p);
    ok('Pille auch nach Neuladen', !!r.pill && !r.kurz, r);
    const B = await login(br, cfg.an, cfg.pw); await heute(B.p);
    const r2 = await read(B.p);
    ok('Pille im zweiten Kontext (andere Sitzung)', !!r2.pill && !r2.kurz, r2);
    // Wieder an
    await p.click('#hz-kurz .hz-kurz-undo'); await until(async () => !!(await read(p)).kurz, 4000);
    r = await read(p);
    ok('"Wieder an" -> Zeile wieder da', !!r.kurz && !r.pill, r);
    // 3x ✕ in 14 Tagen -> genau einmal die Frage
    await p.click('#hz-kurz .hz-kurz-x'); await until(async () => !!(await read(p)).pill, 4000);
    await p.click('#hz-kurz .hz-kurz-undo'); await until(async () => !!(await read(p)).kurz, 4000);
    await p.click('#hz-kurz .hz-kurz-x'); await until(async () => { const x = await read(p); return x.ask || x.pill; }, 4000);
    r = await read(p);
    ok('drittes ✕ -> Frage "Tageszeile ausblenden?" statt der Pille', !!r.ask && !r.pill && r.ask.text === 'Tageszeile ausblenden?' && r.ask.yes === 'Ja, ausblenden' && r.ask.no === 'Nein, passt', r);
    await shot(p, 'nachher-frage-desktop-hell');
    await p.click('#hz-kurz .hz-kurz-no'); await until(async () => !!(await read(p)).pill, 4000);
    r = await read(p);
    ok('"Nein, passt" -> Pille', !!r.pill && !r.ask, r);
    await p.click('#hz-kurz .hz-kurz-undo'); await until(async () => !!(await read(p)).kurz, 4000);
    await p.click('#hz-kurz .hz-kurz-x'); await until(async () => { const x = await read(p); return x.ask || x.pill; }, 4000);
    r = await read(p);
    ok('Frage danach nie wieder (viertes ✕ -> Pille)', !!r.pill && !r.ask, r);
    await p.click('#hz-kurz .hz-kurz-undo'); await until(async () => !!(await read(p)).kurz, 4000);

    // ⋯: Warum sehe ich das? / Tageszeile ausblenden
    await p.click('#hz-kurz .hz-kurz-m'); await p.waitForTimeout(250);
    const items = await p.evaluate(() => [...document.querySelectorAll('.menu:not([hidden]) .menu-item')].map((x) => x.textContent.trim()));
    ok('⋯-Menue: "Tageszeile ausblenden", "Warum sehe ich das?"', same(items, ['Tageszeile ausblenden', 'Warum sehe ich das?']), items);
    await p.click('.menu:not([hidden]) .menu-item >> text=Warum sehe ich das?'); await p.waitForTimeout(250);
    const info = await p.evaluate(() => { const i = document.getElementById('hz-kurz-info'); return i ? { t: i.textContent, f: i.contains(document.activeElement) } : null; });
    ok('ⓘ zeigt info des Servers ("Ohne KI berechnet …")', !!info && /Ohne KI berechnet/.test(info.t) && info.f, info);
    await p.click('#hz-kurz-info .hz-kurz-ix'); await p.waitForTimeout(150);
    ok('ⓘ schliessen -> Fokus zurueck auf ⋯', await p.evaluate(() => !document.getElementById('hz-kurz-info') && document.activeElement.classList.contains('hz-kurz-m')));
    await p.click('#hz-kurz .hz-kurz-m'); await p.waitForTimeout(250);
    await p.click('.menu:not([hidden]) .menu-item >> text=Tageszeile ausblenden'); await until(async () => !(await read(p)).kurz, 4000);
    r = await read(p);
    ok('"Tageszeile ausblenden" -> Zeile weg, keine Pille, "Wartet" bleibt', !r.kurz && !r.pill && !r.ask && !!r.waiting, r);
    // Einstellungen › Saga: Schalter aus -> an
    await p.evaluate(() => window.HKSettings.open('saga'));
    const sw = await until(() => p.$('#hk-saga-today'), 6000);
    const swSt = sw ? await p.evaluate(() => { const i = document.getElementById('hk-saga-today'); return { on: i.checked, lab: i.closest('label').textContent, hint: (document.getElementById('hk-saga-today-h') || {}).textContent }; }) : null;
    ok('Einstellungen › Saga: Schalter "Tageszeile auf „Heute“" (aus), Hinweis = info', !!swSt && !swSt.on && /Tageszeile auf „Heute“/.test(swSt.lab) && /Ohne KI berechnet/.test(swSt.hint || ''), swSt);
    if (sw) { await sw.click(); await p.waitForTimeout(900); }
    await p.evaluate(() => window.HKSettings.close()); await p.waitForTimeout(300);
    r = await read(p);
    ok('Schalter an -> Zeile auf "Heute" sofort wieder da', !!r.kurz, r);

    // Live aus einem zweiten Tab (ai.today.changed vom Server, S3)
    const p2 = await A.ctx.newPage(); await p2.goto(BASE + '/dashboard'); await p2.waitForTimeout(800);
    const before = (await read(p)).kurz;
    const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Berlin' });
    await api(p2, 'POST', '/api/tasks', { text: 'Zweiter Tab ' + Date.now().toString(36), due: today });
    const done = await until(async () => { const x = (await read(p)).kurz; return x && !same(x.parts, before && before.parts) ? x : null; }, 5000);
    if (done) ok('Live aus zweitem Tab: Zeile aktualisiert ohne Neuladen', true);
    else offen('Live aus zweitem Tab (ai.today.changed kommt erst mit kd-srv S3)', before && before.parts);
    await p2.close();

    await A.ctx.close(); await B.ctx.close();
    // dunkel (eigener Kontext: das Init-Skript setzt das Theme bei jedem Laden)
    const D = await login(br, cfg.an, cfg.pw, { theme: 'dark' }); p = D.p; await heute(p);
    ok('dunkel wirksam', await p.evaluate(() => document.documentElement.dataset.theme === 'dark'));
    k = await con();
    ok('Kontrast dunkel >= 4,5 (Teil, Label, Wartet; ✕ >= 3)', k.p >= 4.5 && k.l >= 4.5 && k.w >= 4.5 && k.x >= 3, k);
    await shot(p, 'nachher-heute-desktop-dunkel');
    await p.click('#hz-kurz .hz-kurz-x'); await until(async () => !!(await read(p)).pill, 4000);
    k = await p.evaluate((C) => { const f = eval(C), e = document.querySelector('.hz-kurz-pt'), u = document.querySelector('.hz-kurz-undo'), pl = document.querySelector('.hz-kurz-pill');
      const bg = getComputedStyle(pl).backgroundColor; return { t: +f(getComputedStyle(e).color, bg).toFixed(2), u: +f(getComputedStyle(u).color, bg).toFixed(2) }; }, CONTRAST);
    ok('Pille dunkel: Kontrast >= 4,5', k.t >= 4.5 && k.u >= 4.5, k);
    await shot(p, 'nachher-pille-desktop-dunkel');
    await p.click('#hz-kurz .hz-kurz-undo'); await until(async () => !!(await read(p)).kurz, 4000);
    await D.ctx.close();

    // ================= 3. mobil 390 px, 200 % Zoom =================
    for (const [nm, vp, dsf] of [['390 px', { width: 390, height: 844 }, 2], ['200 % Zoom (1440 -> 720 CSS-px)', { width: 720, height: 450 }, 2]]) {
      const M = await login(br, cfg.an, cfg.pw, { viewport: vp, dsf, theme: 'light' });
      await heute(M.p);
      const m = await M.p.evaluate(() => {
        const b = document.getElementById('hz-kurz-box'), W = document.documentElement.clientWidth;
        const els = [...b.querySelectorAll('button, a')], rs = els.map((e) => e.getBoundingClientRect());
        return { W, sw: document.scrollingElement.scrollWidth, right: Math.max(...rs.map((x) => x.right)), minH: Math.min(...rs.map((x) => x.height)),
          minW: Math.min(...rs.map((x) => x.width)), vis: !b.hidden, n: els.length };
      });
      ok(`${nm}: kein waagrechter Ueberlauf, alles sichtbar`, m.vis && m.sw <= m.W + 1 && m.right <= m.W + 1, m);
      if (vp.width === 390) {
        ok('390 px: Knoepfe >= 44 px', m.minH >= 44 && m.minW >= 44, m);
        await shot(M.p, 'nachher-heute-mobil-hell');
        await M.p.click('#hz-kurz .hz-kurz-x'); await until(async () => !!(await read(M.p)).pill, 4000);
        await shot(M.p, 'nachher-pille-mobil-hell');
        await M.p.click('#hz-kurz .hz-kurz-undo'); await until(async () => !!(await read(M.p)).kurz, 4000);
      }
      await M.ctx.close();
    }

    // ================= 4. Thai: Zeilenhoehe >= 1,6 =================
    {
      const T = await login(br, cfg.an, cfg.pw, { lang: 'th' });
      await mock(T.p, 200, caseOf('th').response); await heute(T.p);
      const th = await T.p.evaluate(() => { const e = document.querySelector('#hz-kurz .hz-kurz-p'), s = getComputedStyle(e);
        return { lang: document.documentElement.lang, lh: parseFloat(s.lineHeight) / parseFloat(s.fontSize), w: (document.querySelector('#hz-wait .hz-wait-a') || {}).textContent }; });
      const tr = await read(T.p);
      ok('Thai: Zeilenhoehe >= 1,6 und Anzeige = show', th.lang === 'th' && th.lh >= 1.6 && same(seen(tr), showOf(caseOf('th').show)), { th, tr: seen(tr) });
      await T.ctx.close();
    }

    // ================= 5. Sie-Konto, ohne ai =================
    {
      const S = await login(br, cfg.sie, cfg.pw);
      const pr = await api(S.p, 'PUT', '/api/ai/prefs', { salutation: 'sie' });
      await heute(S.p); const rs = await read(S.p);
      ok('Sie-Konto: "… warten auf Sie ›", Zeile mit "heute"', pr.s === 200 && /warten auf Sie ›$/.test(rs.waiting || '') && !!rs.kurz && /heute/.test(rs.kurz.parts.join(' ')), { pr: pr.s, rs });
      await S.ctx.close();
      const O = await login(br, cfg.aus, cfg.pw);
      let reqs = 0; O.p.on('request', (q) => { if (/\/api\/ai\/today/.test(q.url())) reqs++; });
      await heute(O.p); await O.p.waitForTimeout(500);
      const o = await O.p.evaluate(() => ({ box: !!document.getElementById('hz-kurz-box'), kids: [...document.getElementById('hz-lage').children].map((x) => x.id),
        next: document.getElementById('hz-lage').nextElementSibling.className, lage: document.getElementById('hz-lage').textContent }));
      ok('ohne ai: kein Element, keine Anfrage, Lagezeile unveraendert (Datum + Zahlen)', !o.box && !reqs && same(o.kids, ['hz-date', 'hz-parts']) && o.next === 'hz-acts', { o, reqs });
      await O.ctx.close();
    }
  } finally {
    await br.close();
    if (!E.KEEP) py('--teardown');
  }
  if (errs.length) ok('keine Skriptfehler', false, errs.slice(0, 5));
  const bad = R.filter((x) => !x[1]).length;
  console.log(`\n${R.length - bad}/${R.length} OK` + (OFFEN.length ? `, ${OFFEN.length} offen: ${OFFEN.join('; ')}` : '') + (FF ? ' (Firefox)' : ' (Chromium)'));
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); try { if (!E.KEEP) py('--teardown'); } catch (x) { /* egal */ } process.exit(1); });
