// Browser-Ende-zu-Ende fuer die Anruf-Huelle (2.5, static/shell.js): ein Anruf auf /chats bleibt bestehen, wenn man zu
// anderen Saga-Seiten wechselt (Seite aus der Seitenleiste, Heute, Aufgaben, Zurueck/Vor, wieder Chats). Zwei Playwright-
// Kontexte (zwei frische Konten) im headless Chromium mit Kamera/Mikrofon-Attrappe, gegen einen Test-Stack:
//   SAGA_BASE=http://host:port SAGA_USER=konto-a SAGA_PASS=... SAGA_USER2=konto-b SAGA_PASS2=... [SHOTS=/pfad] \
//   [PLAYWRIGHT=/pfad/zu/playwright-core] [PW_CHANNEL=chromium] node tests/browser_anruf_global.js
// Geprueft: Server-Zustand des Anrufs (GET /api/calls/<id>) und Verbindung der Gegenseite bleiben ueber alle Wechsel,
// Adresszeile und Titel folgen der Seite im Rahmen, die eigene Oberflaeche der Huelle ist aus, im Rahmen klingelt/
// telefoniert nichts (kein calls.js, kein /end beim Verlassen), kein beforeunload-Dialog, Auflegen aus der Huelle, nach
// dem Anruf bleibt die Seite stehen und der naechste Wechsel laedt wieder normal. Einbetten nur gleicher Herkunft.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '';
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, name + '.png') }); } }
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn().catch(() => null); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const ERR = [], DIALOGS = [], ENDS = [];
const TITLE = 'Anruf-Huelle Testseite ' + (Date.now() % 1000000);

async function session(browser, user, pass, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: 'dark' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.on('pageerror', e => { ERR.push(user + ': ' + e.message); console.log('  [pageerror ' + user + '] ' + e.message); });
  p.on('dialog', d => { DIALOGS.push(user + ': ' + d.type()); d.dismiss().catch(() => {}); });
  p.on('request', r => { if (/\/api\/calls\/[0-9a-f]{32}\/end$/.test(r.url())) ENDS.push({ user, frame: r.frame() !== p.mainFrame() }); });
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + user + '] ' + m.text()));
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', pass);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  return p;
}
const RKF = u => path.join(os.tmpdir(), 'saga-browser-rk-' + BASE.replace(/\W+/g, '_') + '-' + u + '.txt');
async function chatKeys(q, user) {
  await q.goto(BASE + '/chats'); await q.waitForTimeout(2500);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim(); fs.writeFileSync(RKF(user), rk, { mode: 0o600 });
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 60000 });
  } else if (await q.$('.ct-rk-go') && fs.existsSync(RKF(user))) {
    await q.click('.ct-rk-go'); await q.fill('.ct-rk-in', fs.readFileSync(RKF(user), 'utf8').trim());
    await q.click('.ct-rk-ok'); await q.waitForSelector('.ct-rk-ok', { state: 'detached', timeout: 60000 });
  }
  await q.waitForTimeout(800);
}
const st = p => p.evaluate(() => window.HKCalls && window.HKCalls.state());
const rel = p => { const u = new URL(p.url()); return u.pathname + u.search + u.hash; };
async function frameOf(p) { const h = await p.$('#hk-shell-frame'); return h && await h.contentFrame(); }
async function framePath(p) { const f = await frameOf(p); return f ? f.evaluate(() => location.pathname + location.search + location.hash) : null; }
// Server-Zustand (beide Seiten) und Verbindung der Gegenseite
async function alive(a, b, id) {
  const srv = await a.evaluate(cid => fetch('/api/calls/' + cid, { headers: { 'X-Saga': '1' } }).then(r => r.json()).then(r => r.call && r.call.state), id);
  const sa = await st(a), sb = await st(b);
  return { srv, a: !!(sa && sa.connected && sa.id === id), b: !!(sb && sb.connected && sb.id === id) };
}
const allOk = x => x && x.srv === 'active' && x.a && x.b;
// Reste frueherer Laeufe (abgebrochener Anruf -> 409 besetzt) beenden
const cleanup = p => p.evaluate(() => fetch('/api/calls/active', { headers: { 'X-Saga': '1' } }).then(r => r.json()).then(d => Promise.all((d.calls || [])
  .map(c => fetch('/api/calls/' + c.id + '/end', { method: 'POST', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({ reason: 'hangup' }) }))))).catch(() => {});

(async () => {
  const origin = new URL(BASE).origin;
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin,
           '--autoplay-policy=no-user-gesture-required'] });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2);
    // eine Seite fuer die Seitenleiste (vor dem Laden der Chat-Seite)
    await a.goto(BASE + '/dashboard');
    const note = await a.evaluate(TITLE => fetch('/api/notes', { method: 'POST', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: TITLE }) }).then(r => r.json()), TITLE);
    const nid = note && (note.id || (note.note && note.note.id));
    ok('Testseite angelegt', nid > 0, note);
    await b.goto(BASE + '/dashboard'); await cleanup(a); await cleanup(b); ENDS.length = 0;
    await chatKeys(a, U1); await chatKeys(b, U2);
    ok('Chat-Schluessel in beiden Browsern bereit', !(await a.$('.ct-ov')) && !(await b.$('.ct-ov')));

    // --- Anruf aufbauen: A ruft B aus dem Direkt-Chat an, B nimmt auf /chats an ----------------------------------------
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);
    await b.goto(BASE + '/chats'); await b.waitForTimeout(2000);
    await a.click('#ct-call-a');
    await b.waitForSelector('.cl-ring', { timeout: 15000 }).catch(() => {});
    ok('B: Klingeln auf /chats', await b.isVisible('.cl-ring'));
    await b.click('.cl-ring .cl-big');
    const con = await until(async () => { const x = await st(a), y = await st(b); return x && y && x.connected && y.connected && x; }, 25000);
    ok('Anruf verbunden (beide Seiten)', !!con, { a: await st(a), b: await st(b) });
    const id = con && con.id;
    ok('Server: Anruf aktiv', allOk(await alive(a, b, id)), await alive(a, b, id));
    ok('Huelle bereit, Anruf gemeldet (HKShell, HKCalls.busy)', await a.evaluate(() => !!(window.HKShell && window.HKCalls.busy() && !window.HKShell.on())));
    await a.click('.cl-shrink'); await a.waitForTimeout(400);
    await shot(a, '01-anruf-verkleinert-chats');

    // --- 1. Seite aus der Seitenleiste der Chat-Seite oeffnen ---------------------------------------------------------
    await a.waitForSelector('#sh-tree .pg-go', { timeout: 10000 }).catch(() => {});
    const go = await a.$('#sh-tree .pg-go:has-text("' + TITLE + '")');
    ok('Seitenleiste zeigt die Testseite', !!go);
    const t1 = Date.now();
    if (go) await go.evaluate(e => e.click());              // das kleine Anruffenster liegt unten links ueber der Leiste
    const f1 = await until(async () => { const p = await framePath(a); return p === '/notes#' + nid && p; }, 30000);
    console.log('  (Rahmen nach ' + (Date.now() - t1) + ' ms)');
    ok('Seite oeffnet sich im Rahmen der Huelle (/notes#id)', f1 === '/notes#' + nid, await framePath(a));
    ok('Adresszeile = Seite im Rahmen', await until(async () => rel(a) === '/notes#' + nid, 5000), rel(a));
    await a.waitForTimeout(1500);
    let x = await alive(a, b, id);
    ok('Nach dem Wechsel zur Seite: Anruf laeuft weiter (Server aktiv, beide verbunden)', allOk(x), x);
    const ui = await a.evaluate(() => {
      const vis = s => { const e = document.querySelector(s); return !!(e && e.getClientRects().length && getComputedStyle(e).display !== 'none'); };
      const w = document.querySelector('.cl-win'), r = w && w.getBoundingClientRect(), hit = r && document.elementFromPoint(r.left + r.width / 2, r.top + 10);
      return { on: document.body.classList.contains('hk-shell-on'), side: vis('#pg-side'), head: vis('.np-head'), main: vis('.ct-main'),
               frame: vis('#hk-shell-frame'), callTop: !!(hit && hit.closest('.cl-win')) };
    });
    ok('Huelle: eigene Seitenleiste/Kopf/Chat aus, Rahmen sichtbar, Anruf-Leiste obenauf', ui.on && !ui.side && !ui.head && !ui.main && ui.frame && ui.callTop, ui);
    const fr = await frameOf(a);
    const inF = fr && await fr.evaluate(() => ({ calls: !!window.HKCalls, ring: document.querySelectorAll('.cl-ring,.cl-win').length, shell: !!(window.HK && window.HK.shell && window.HK.shell()),
                                                   cls: document.documentElement.classList.contains('hk-in-shell'), title: document.title }));
    ok('Im Rahmen: kein Anruf-Skript, kein Klingeln/Anruffenster, weiss von der Huelle', inF && !inF.calls && !inF.ring && inF.shell && inF.cls, inF);
    ok('Titel folgt der Seite im Rahmen', await until(async () => (await a.title()) === inF.title && /Anruf-Huelle Testseite|Seiten/.test(inF.title), 5000), { top: await a.title(), frame: inF && inF.title });
    await shot(a, '02-seite-im-rahmen');

    // --- 2. Im Rahmen: Heute, dann Aufgaben (gewoehnliche Links der Seitenleiste im Rahmen) ---------------------------------
    await fr.click('.pg-nav-row[href="/dashboard"]');
    ok('Heute im Rahmen', await until(async () => (await framePath(a)) === '/dashboard' && rel(a) === '/dashboard', 15000), { f: await framePath(a), top: rel(a) });
    await a.waitForTimeout(1200);
    x = await alive(a, b, id); ok('Auf Heute: Anruf laeuft weiter', allOk(x), x);
    const fr2 = await frameOf(a);
    ok('Heute im Rahmen ohne Anruf-Skript', fr2 && !(await fr2.evaluate(() => !!window.HKCalls)));
    await shot(a, '03-heute-im-rahmen');
    await fr2.click('.pg-nav-row[href="/notes?v=tasks"]');
    // pages.js nimmt ?v=tasks nach dem Einstieg aus der Adresse (replaceState) - die Huelle zieht nach
    ok('Aufgaben im Rahmen (Adresszeile zieht nach)', await until(async () => { const f = await frameOf(a); return f && (await framePath(a)) === '/notes' && rel(a) === '/notes'
       && await f.evaluate(() => !!document.querySelector('#pg-view-body') && document.body.classList.contains('notes-page')); }, 15000), { f: await framePath(a), top: rel(a) });
    await a.waitForTimeout(1200);
    x = await alive(a, b, id); ok('Auf Aufgaben: Anruf laeuft weiter', allOk(x), x);

    // --- 3. Zurueck / Vor ---------------------------------------------------------------------------------------------
    await a.goBack().catch(() => {});
    ok('Zurueck: Heute (Rahmen und Adresszeile)', await until(async () => (await framePath(a)) === '/dashboard' && rel(a) === '/dashboard', 10000), { f: await framePath(a), top: rel(a) });
    await a.goBack().catch(() => {});
    ok('Zurueck: wieder die Seite', await until(async () => (await framePath(a)) === '/notes#' + nid && rel(a) === '/notes#' + nid, 10000), { f: await framePath(a), top: rel(a) });
    await a.goBack().catch(() => {});
    ok('Zurueck: wieder die Chat-Seite (Rahmen aus, Adresse /chats…)', await until(async () => !(await a.evaluate(() => window.HKShell.on())) && /^\/chats/.test(rel(a)), 10000), rel(a));
    await a.goForward().catch(() => {});
    ok('Vor: Seite wieder im Rahmen', await until(async () => (await a.evaluate(() => window.HKShell.on())) && rel(a) === '/notes#' + nid, 10000), rel(a));
    x = await alive(a, b, id); ok('Nach Zurueck/Vor: Anruf laeuft weiter', allOk(x), x);

    // --- 4. Chats aus dem Rahmen: die Huelle zeigt sich selbst ---------------------------------------------------------
    const fr3 = await frameOf(a);
    await fr3.click('.pg-nav-row[href="/chats"]');
    ok('Chats aus dem Rahmen: Huelle zeigt die Chat-Seite (ohne Neuladen)', await until(async () => !(await a.evaluate(() => window.HKShell.on())) && /^\/chats/.test(rel(a))
       && await a.evaluate(() => getComputedStyle(document.querySelector('.ct-main')).display !== 'none'), 8000), rel(a));
    x = await alive(a, b, id); ok('Auf Chats: Anruf laeuft weiter', allOk(x), x);
    // Navigationshelfer der Huelle (Befehlspalette/side.js nutzen HK.go)
    await a.evaluate(() => window.HK.go('/dashboard'));
    ok('HK.go in der Huelle: Heute im Rahmen', await until(async () => (await framePath(a)) === '/dashboard' && rel(a) === '/dashboard', 10000), { f: await framePath(a), top: rel(a) });
    x = await alive(a, b, id); ok('Nach HK.go: Anruf laeuft weiter', allOk(x), x);
    ok('Kein beforeunload-Dialog, kein /end, keine Seitenfehler bis hier', !DIALOGS.length && !ENDS.length && !ERR.length, { DIALOGS, ENDS, ERR });

    // --- 5. Auflegen aus der Huelle ------------------------------------------------------------------------------------
    await a.click('.cl-shrink').catch(() => {}); await a.waitForTimeout(300);
    await a.click('.cl-ctl .cl-red');
    ok('Auflegen aus der Huelle: Gegenueber sieht das Ende', await until(() => b.evaluate(() => !window.HKCalls.state()), 8000));
    const srvEnd = await until(() => a.evaluate(cid => fetch('/api/calls/' + cid, { headers: { 'X-Saga': '1' } }).then(r => r.json()).then(r => r.call.state === 'ended' && r.call.state), id), 8000);
    ok('Server: Anruf beendet', srvEnd === 'ended', srvEnd);
    await a.waitForTimeout(1500);
    ok('Nach dem Anruf: Seite im Rahmen bleibt stehen (kein Neuladen)', (await a.evaluate(() => window.HKShell.on())) && (await framePath(a)) === '/dashboard' && rel(a) === '/dashboard',
       { f: await framePath(a), top: rel(a) });
    await shot(a, '04-nach-dem-anruf');
    // naechster Wechsel aus dem Rahmen: normal im obersten Fenster
    const fr4 = await frameOf(a);
    await Promise.all([a.waitForNavigation({ timeout: 15000 }).catch(() => {}), fr4.click('.pg-nav-row[href="/notes?v=tasks"]')]);
    await a.waitForTimeout(800);
    ok('Nach dem Anruf: naechster Wechsel laedt normal (oberstes Fenster, kein Rahmen)', /^\/notes(\?v=tasks)?$/.test(rel(a)) && !(await a.$('#hk-shell-frame')) && !(await a.evaluate(() => !!window.HKShell)),
       { top: rel(a) });

    // --- 6. Ohne Anruf: Chat-Seite laedt Seiten wie immer ---------------------------------------------------------------
    await a.goto(BASE + '/chats'); await a.waitForTimeout(1500);
    await Promise.all([a.waitForNavigation({ timeout: 15000 }).catch(() => {}), a.click('#pg-side a.pg-nav-row[href="/dashboard"]')]);
    ok('Ohne Anruf: Heute aus den Chats laedt normal', rel(a) === '/dashboard' && !(await a.$('#hk-shell-frame')), rel(a));

    // --- 7. Handy (390 px): Tab-Leiste der Huelle -> Heute im Rahmen, Tab-Leiste im Rahmen -> Chats --------------------
    await a.setViewportSize({ width: 390, height: 844 });
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);
    await b.goto(BASE + '/chats'); await b.waitForTimeout(2000);
    await a.click('#ct-call-a');
    await b.waitForSelector('.cl-ring', { timeout: 15000 }).catch(() => {});
    await b.click('.cl-ring .cl-big').catch(() => {});
    const con2 = await until(async () => { const x2 = await st(a), y2 = await st(b); return x2 && y2 && x2.connected && y2.connected && x2; }, 25000);
    ok('Handy: Anruf verbunden', !!con2);
    const id2 = con2 && con2.id;
    await a.click('.cl-shrink').catch(() => {}); await a.waitForTimeout(400);
    // Zurueck aus dem Chat zur Liste, dann Tab "Heute" (die Tab-Leiste ist nur in der Liste sichtbar)
    const tab = await a.$('#mob-tabs a[href="/dashboard"]');
    if (tab) await tab.evaluate(e => e.click());
    ok('Handy: Tab "Heute" oeffnet im Rahmen', await until(async () => (await framePath(a)) === '/dashboard' && rel(a) === '/dashboard', 15000), { f: await framePath(a), top: rel(a) });
    const mob = await a.evaluate(() => ({ tabs: getComputedStyle(document.getElementById('mob-tabs')).display, fw: document.getElementById('hk-shell-frame').getBoundingClientRect().width }));
    ok('Handy: eigene Tab-Leiste der Huelle aus, Rahmen volle Breite', mob.tabs === 'none' && mob.fw >= 389, mob);
    const frm = await frameOf(a);
    const ftab = frm && await frm.evaluate(() => { const t = document.querySelector('#mob-tabs a[href="/chats"]'); return !!(t && t.getClientRects().length); });
    ok('Handy: Tab-Leiste im Rahmen sichtbar', ftab);
    await shot(a, '05-handy-heute-im-rahmen');
    let x2 = await alive(a, b, id2); ok('Handy: auf Heute laeuft der Anruf weiter', allOk(x2), x2);
    if (frm) await frm.evaluate(() => document.querySelector('#mob-tabs a[href="/chats"]').click());
    ok('Handy: Tab "Chats" im Rahmen zeigt die Huelle', await until(async () => !(await a.evaluate(() => window.HKShell.on())) && /^\/chats/.test(rel(a)), 8000), rel(a));
    x2 = await alive(a, b, id2); ok('Handy: zurueck in den Chats laeuft der Anruf weiter', allOk(x2), x2);
    await a.click('.cl-shrink').catch(() => {}); await a.waitForTimeout(300);
    await a.click('.cl-ctl .cl-red').catch(() => {});
    ok('Handy: Auflegen', await until(() => b.evaluate(() => !window.HKCalls.state()), 8000));
    await a.setViewportSize({ width: 1280, height: 800 });

    // --- 8. Einbetten: nur Rahmen gleicher Herkunft ---------------------------------------------------------------------
    const hdr = await a.evaluate(() => fetch('/notes').then(r => ({ csp: r.headers.get('content-security-policy'), xfo: r.headers.get('x-frame-options') })));
    ok('Seiten als Dokument weiter nicht einbettbar (frame-ancestors none / DENY)', /frame-ancestors 'none'/.test(hdr.csp || '') && hdr.xfo === 'DENY', hdr);
    ok('Am Ende: kein beforeunload-Dialog, kein /end aus einem Rahmen, keine Seitenfehler', !DIALOGS.length && !ENDS.some(e => e.frame) && !ERR.length, { DIALOGS, ENDS, ERR });
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e));
  } finally {
    await browser.close();
  }
  const n = res.filter(Boolean).length;
  console.log(n === res.length ? `ALLES GRUEN (${n}/${res.length})` : `FEHLER: ${res.length - n} von ${res.length}`);
  process.exit(n === res.length ? 0 : 1);
})();
