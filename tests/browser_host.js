// Saga 2.5 (Desktop-Huelle P1) - Brueckenvertrag window.sagaHost (static/host.js, docs/APP-API-HOST.md):
// ohne Huelle No-ops ohne Fehler; simulierte Mac-Huelle (UA SagaApp/…, webkit.messageHandlers.hk als Aufzeichner) und
// simulierte Tauri-Huelle (__TAURI_INTERNALS__.invoke) bekommen badge / widgets.reload (entprellt) / notify / openExternal /
// ready; alte native App (HlidskjalfApp/…, ohne Meldung) nur settings+token; Deep-Link-Abbildung und -Routen (Seite,
// Aufgaben, neue Aufgabe, Termine, Einstellungen); Offline-Seite ohne CSP-Fehler (Hashes, Hell/Dunkel, Sprache) und als
// Rueckfall des Service Workers bei Navigationsfehlern.
//   SAGA_BASE=… SAGA_USER=… SAGA_PASS=… SAGA_USER2=… SAGA_PASS2=… node tests/browser_host.js
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const crypto = require('crypto'), fs = require('fs'), path = require('path');
const E = process.env, BASE = E.SAGA_BASE, USER = E.SAGA_USER, PASS = E.SAGA_PASS, USER2 = E.SAGA_USER2, PASS2 = E.SAGA_PASS2;
if (!BASE || !USER || !PASS || !USER2 || !PASS2) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS, SAGA_USER2, SAGA_PASS2 setzen'); process.exit(2); }
const UA_CHROME = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)';
const CAPS = ['badge', 'notify', 'widgets', 'openExternal', 'settings', 'token'];

// Aufzeichner fuer die Huellen (laeuft vor jedem Dokument)
function macInit(arg) {
  window.__hostMsgs = [];
  window.webkit = { messageHandlers: { hk: { postMessage: m => window.__hostMsgs.push(JSON.parse(JSON.stringify(m))) } } };
  if (arg && arg.info) window.sagaHostInfo = arg.info;
}
function tauriInit(arg) {
  window.__hostMsgs = [];
  window.__TAURI_INTERNALS__ = { invoke: (cmd, args) => { window.__hostMsgs.push({ cmd, msg: JSON.parse(JSON.stringify(args && args.msg)) }); return Promise.resolve(null); } };
  if (arg && arg.info) window.sagaHostInfo = arg.info;
}

(async () => {
  const b = await chromium.launch({ channel: E.PW_CHANNEL || undefined }), res = [];
  const ok = (c, m, x) => { res.push([!!c, m]); console.log((c ? 'OK   ' : 'FAIL ') + m + (c || x === undefined ? '' : ' ' + JSON.stringify(x))); };
  async function ctxFor(ua, init, arg, extra) {
    const ctx = await b.newContext(Object.assign({ viewport: { width: 1300, height: 860 }, locale: 'de-DE', userAgent: ua }, extra || {}));
    if (init) await ctx.addInitScript(init, arg || null);
    return ctx;
  }
  async function login(ctx, u, pw) {
    const p = await ctx.newPage();
    p.__errs = []; p.on('pageerror', e => p.__errs.push(String(e)));
    p.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) p.__errs.push('console: ' + m.text()); });
    await p.goto(BASE + '/login'); await p.fill('input[name=username]', u); await p.fill('input[name=password]', pw);
    await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
    return p;
  }
  const msgs = p => p.evaluate(() => (window.__hostMsgs || []).slice());
  const clear = p => p.evaluate(() => { window.__hostMsgs = []; });
  const wait = ms => new Promise(r => setTimeout(r, ms));

  // --- 1. Ohne Huelle ---------------------------------------------------------------------------------------------------
  {
    const ctx = await ctxFor(UA_CHROME + ' Chrome/130.0 Safari/537.36'), p = await login(ctx, USER, PASS);
    for (const u of ['/', '/notes', '/chats']) {
      await p.goto(BASE + u); await p.waitForTimeout(1500);
      const s = await p.evaluate(() => ({ h: !!window.HK && !!HK.host, same: window.sagaHost === (window.HK && HK.host), kind: HK.host && HK.host.kind,
        caps: HK.host && HK.host.caps.length, badge: HK.host.badge(3), notify: HK.host.notify('a', 'b', '/'), w: HK.host.widgetsReload(),
        post: HK.host.post({ action: 'badge', n: 1 }), hello: HK.host.receive({ type: 'hello', kind: 'mac', caps: ['badge'] }),
        open: HK.host.receive({ type: 'open', link: 'saga://tasks' }), wo: window.open === window.open }));
      ok(s.h && s.same && s.kind === null && s.caps === 0, u + ': HK.host = window.sagaHost, keine Huelle', s);
      ok(s.badge === false && s.notify === false && s.w === false && s.post === false && s.hello === false && s.open === false, u + ': alle Aufrufe No-ops', s);
    }
    // openExternal ohne Huelle: neues Fenster wie bisher
    const [pop] = await Promise.all([ctx.waitForEvent('page', { timeout: 5000 }).catch(() => null), p.evaluate(() => HK.host.openExternal('https://example.invalid/x'))]);
    ok(!!pop, 'ohne Huelle: openExternal oeffnet ein neues Fenster');
    if (pop) await pop.close();
    ok(!(await p.evaluate(() => HK.host.openExternal('javascript:alert(1)'))), 'openExternal: nur http(s)/mailto');
    // Mitteilungen im Browser unveraendert (kein Hinweis auf die App)
    await p.goto(BASE + '/notes?settings=push'); await p.waitForTimeout(1800);
    const pt = await p.evaluate(() => (document.getElementById('hk-pane-push') || {}).textContent || '');
    ok(!/über die App selbst/.test(pt), 'Browser: Mitteilungen ohne App-Hinweis');
    // Deep-Link-Abbildung (reine Funktion, auch ohne Huelle)
    const R = await p.evaluate(() => ['saga://page/12', 'hlidskjalf://page/12', 'saga://today', 'saga://', 'saga://tasks', 'saga://events',
      'saga://new-task', 'saga://new-task?src=5', 'saga://new-page', 'hlidskjalf://new?parent=12', 'hlidskjalf://new?db=7&view=tab&k=x',
      'saga://settings/push', 'saga://settings', 'saga://settings/../x', 'saga://chats', 'saga://auth?code=x', 'saga://pair?u=x&c=y',
      'saga://page/abc', 'https://example.org/', 'javascript:alert(1)'].map(l => [l, HK.host.route(l)]));
    const want = { 'saga://page/12': '/notes#12', 'hlidskjalf://page/12': '/notes#12', 'saga://today': '/', 'saga://': '/', 'saga://tasks': '/notes?v=tasks',
      'saga://events': '/notes?v=events', 'saga://new-task': '/notes?v=tasks&new=task', 'saga://new-task?src=5': '/notes?v=tasks&new=task',
      'saga://new-page': '/notes?new=blank', 'hlidskjalf://new?parent=12': '/notes#12', 'hlidskjalf://new?db=7&view=tab&k=x': '/notes#7',
      'saga://settings/push': '/notes?settings=push', 'saga://settings': '/notes?settings=account', 'saga://settings/../x': '/notes?settings=account',
      'saga://chats': '/chats', 'saga://auth?code=x': null, 'saga://pair?u=x&c=y': null, 'saga://page/abc': null, 'https://example.org/': null, 'javascript:alert(1)': null };
    const bad = R.filter(([l, r]) => want[l] !== r);
    ok(!bad.length, 'Deep-Link-Abbildung (' + R.length + ' Faelle)', bad);
    ok(!p.__errs.length, 'ohne Huelle: keine Fehler', p.__errs);
    await ctx.close();
  }

  // Seite von A fuer Deep-Links; Konto B teilt spaeter eine Seite (Mitteilung)
  const ctxB = await ctxFor(UA_CHROME + ' Chrome/130.0'), pB = await login(ctxB, USER2, PASS2);

  // --- 2. Mac-Huelle ----------------------------------------------------------------------------------------------------
  {
    const ctx = await ctxFor(UA_CHROME + ' SagaApp/3.0 (macOS)', macInit, { info: { kind: 'mac', version: '3.0', caps: CAPS.concat(['unbekannt']) } });
    const p = await login(ctx, USER, PASS);
    await p.goto(BASE + '/notes'); await p.waitForTimeout(2500);
    let s = await p.evaluate(() => ({ kind: HK.host.kind, v: HK.host.version, caps: HK.host.caps }));
    ok(s.kind === 'mac' && s.v === '3.0' && s.caps.join() === 'badge,notify,widgets,openExternal,settings,token', 'Mac: Art, Version, Faehigkeiten (Unbekanntes verworfen)', s);
    let m = await msgs(p);
    ok(m.some(x => x.action === 'ready' && x.v === 1 && x.path === '/notes'), 'Mac: ready mit Pfad', m);
    ok(m.some(x => x.action === 'badge' && typeof x.n === 'number'), 'Mac: badge nach dem Zaehlen der Glocke', m);
    const n0 = (m.filter(x => x.action === 'badge').pop() || {}).n;
    ok(m.filter(x => x.action === 'badge').length === 1, 'Mac: badge nur bei Aenderung', m.filter(x => x.action === 'badge'));
    // Aufgabe anlegen -> widgets.reload sofort; zweite gleich danach -> zusammengefasst nach 10 s
    await clear(p);
    const tid = await p.evaluate(() => HK.api('POST', '/api/tasks', { text: 'Bruecke Test 1' }).then(d => d.id));
    await p.evaluate(() => HK.api('POST', '/api/tasks', { text: 'Bruecke Test 2' }));
    await p.waitForTimeout(300);
    m = await msgs(p);
    ok(m.filter(x => x.action === 'widgets.reload').length === 1, 'Mac: widgets.reload nach Aufgabe anlegen (einmal sofort)', m);
    await p.evaluate(() => HK.api('GET', '/api/tasks')); await p.evaluate(() => HK.api('POST', '/api/notes/search', { q: 'x' }).catch(() => null));
    await p.waitForTimeout(10500);
    m = await msgs(p);
    ok(m.filter(x => x.action === 'widgets.reload').length === 2, 'Mac: weitere Aenderungen im 10-s-Fenster -> genau ein Nachzuegler', m);
    // welche Schreibzugriffe anstossen (Zuordnung in host.js, ohne Entprellung gezaehlt)
    const map = await p.evaluate(() => {
      const o = HK.host.widgetsReload; let n = 0; HK.host.widgetsReload = () => { n++; return true; };
      const c = (m, u) => { n = 0; HK.host._changed(m, u); return n; };
      const r = { task: c('PATCH', '/api/tasks/5'), tnew: c('POST', '/api/tasks'), ev: c('POST', '/api/calendar/events'), note: c('PATCH', '/api/notes/12'),
        nnew: c('POST', '/api/notes'), move: c('POST', '/api/notes/12/move'), trash: c('POST', '/api/trash/3/restore'), gal: c('POST', '/api/gallery/2/use'),
        search: c('POST', '/api/notes/search'), com: c('POST', '/api/notes/12/comments'), prev: c('POST', '/api/notes/12/widgets/preview'),
        digest: c('PUT', '/api/tasks/digest'), get: c('GET', '/api/tasks'), chat: c('POST', '/api/chats/1/messages'), acc: c('PUT', '/api/account/lang'),
        foreign: c('POST', 'https://example.invalid/api/tasks') };
      HK.host.widgetsReload = o; return r;
    });
    ok(['task', 'tnew', 'ev', 'note', 'nnew', 'move', 'trash', 'gal'].every(k => map[k] === 1) &&
       ['search', 'com', 'prev', 'digest', 'get', 'chat', 'acc', 'foreign'].every(k => map[k] === 0), 'Mac: Aufgaben/Termine/Seiten stossen an, Suche/Kommentare/Chat/Konto nicht', map);
    // Externe Links: fremder Link -> openExternal, kein neues Fenster; window.open fremd -> openExternal; eigener Link nicht
    await clear(p);
    let popped = false; const onPage = () => { popped = true; }; ctx.on('page', onPage);
    await p.evaluate(() => { const a = document.createElement('a'); a.href = 'https://example.invalid/doc?a=1'; a.target = '_blank'; a.id = 'xt-link'; a.textContent = 'x'; a.style.cssText = 'position:fixed;left:600px;top:400px;z-index:99999;padding:10px;background:#fff'; document.body.appendChild(a); });
    await p.click('#xt-link');
    const wo = await p.evaluate(() => window.open('https://example.invalid/w') === null);
    await p.waitForTimeout(500);
    m = await msgs(p);
    ok(m.some(x => x.action === 'openExternal' && x.url === 'https://example.invalid/doc?a=1'), 'Mac: Klick auf fremden Link -> openExternal', m);
    ok(wo && m.some(x => x.action === 'openExternal' && x.url === 'https://example.invalid/w'), 'Mac: window.open fremd -> openExternal (null)', m);
    ok(!popped, 'Mac: kein Browserfenster fuer fremde Links');
    ctx.off('page', onPage);
    ok(!(await p.evaluate(() => HK.host.openExternal('/notes#1'))), 'Mac: openExternal nimmt keine eigenen Adressen');
    // Mitteilung live (Seite von B mit A geteilt) -> notify
    await clear(p);
    const nid = await pB.evaluate(() => HK.api('POST', '/api/notes', { title: 'Bruecke geteilt' }).then(d => d.id));
    await pB.evaluate(([id, u]) => HK.api('PUT', '/api/notes/' + id + '/shares', { type: 'user', target: u, role: 'view' }), [nid, USER]);
    await p.waitForTimeout(3000);
    m = await msgs(p);
    const nt = m.filter(x => x.action === 'notify');
    ok(nt.length === 1 && nt[0].v === 1 && nt[0].kind === 'share.added' && /geteilt/.test(nt[0].body) && nt[0].url.indexOf('/') === 0 && nt[0].tag && nt[0].id > 0,
       'Mac: Live-Mitteilung -> notify (Titel, Text, eigener Pfad, Tag, Kennung)', nt);
    ok(m.some(x => x.action === 'badge' && x.n === (n0 || 0) + 1), 'Mac: badge zaehlt mit', m.filter(x => x.action === 'badge'));
    // Bereich abgeschaltet -> kein notify
    await p.evaluate(() => HK.api('PUT', '/api/push/settings', { cats: { share: false } }));
    await p.evaluate(() => { /* Einstellungen frisch holen */ });
    await p.reload(); await p.waitForTimeout(2000); await clear(p);
    await pB.evaluate(([id, u]) => HK.api('PUT', '/api/notes/' + id + '/shares', { type: 'user', target: u, role: 'edit' }), [nid, USER]);
    await p.waitForTimeout(3000);
    m = await msgs(p);
    ok(!m.some(x => x.action === 'notify') && m.some(x => x.action === 'badge'), 'Mac: Bereich "Teilen" aus -> keine System-Mitteilung (Glocke zaehlt weiter)', m);
    await p.evaluate(() => HK.api('PUT', '/api/push/settings', { cats: { share: true } }));
    // Mitteilungs-Einstellungen: kein Browser-Abo in der Huelle
    await p.goto(BASE + '/notes?settings=push'); await p.waitForTimeout(1800);
    const pt = await p.evaluate(() => (document.getElementById('hk-pane-push') || {}).textContent || '');
    ok(/über die App selbst/.test(pt) && !/In diesem Browser einschalten/.test(pt), 'Mac: Mitteilungen ohne Browser-Abo, Hinweis auf die App');
    await p.keyboard.press('Escape');
    // App-Einstellungen im Benutzermenue -> {action:'settings'}
    await clear(p);
    await p.click('#hk-user'); await p.waitForTimeout(300);
    const appItem = await p.$('.menu-item:has-text("App-Einstellungen")');
    ok(!!appItem, 'Mac: Benutzermenue mit "App-Einstellungen"');
    if (appItem) { await appItem.click(); await p.waitForTimeout(200); m = await msgs(p); ok(m.some(x => x.action === 'settings'), 'Mac: App-Einstellungen -> settings', m); }
    // Deep-Links von der Huelle
    const pid = await p.evaluate(() => HK.api('POST', '/api/notes', { title: 'Bruecke Ziel' }).then(d => d.id));
    await p.evaluate(id => sagaHost.receive({ type: 'open', link: 'saga://page/' + id }), pid);
    await p.waitForTimeout(1800);
    s = await p.evaluate(() => ({ u: location.pathname + location.hash, t: (document.getElementById('pg-title') || {}).textContent || (document.getElementById('pg-title') || {}).value }));
    ok(s.u === '/notes#' + pid && /Bruecke Ziel/.test(s.t || ''), 'open saga://page/ID -> Seite', s);
    await p.evaluate(() => sagaHost.receive(JSON.stringify({ type: 'open', link: 'saga://new-task' })));
    await p.waitForTimeout(2500);
    s = await p.evaluate(() => ({ u: location.pathname + location.search, view: !document.getElementById('pg-view').hidden, t: document.getElementById('pg-view-title').textContent,
      f: document.activeElement && document.activeElement.className }));
    ok(s.u === '/notes' && s.view && /Aufgaben/.test(s.t) && /etk-add/.test(s.f || ''), 'open saga://new-task -> Aufgaben, Eingabe fokussiert, Adresse sauber', s);
    await p.evaluate(() => sagaHost.receive({ type: 'open', link: 'saga://events' })); await p.waitForTimeout(2000);
    s = await p.evaluate(() => ({ view: !document.getElementById('pg-view').hidden, t: document.getElementById('pg-view-title').textContent }));
    ok(s.view && /Termine/.test(s.t), 'open saga://events -> Termine', s);
    await p.evaluate(() => sagaHost.receive({ type: 'open', link: 'saga://settings/calendar' })); await p.waitForTimeout(600);
    s = await p.evaluate(() => { const d = document.querySelector('.hk-dialog-set [aria-current="page"]'); return d && d.dataset.k; });
    ok(s === 'calendar', 'open saga://settings/calendar -> Einstellungen Kalender (ohne Neuladen)', s);
    await p.keyboard.press('Escape');
    const before = p.url();
    ok(!(await p.evaluate(() => sagaHost.receive({ type: 'open', path: 'https://example.invalid/' }))) && !(await p.evaluate(() => sagaHost.receive({ type: 'open', link: 'saga://auth?code=x' }))),
       'open: fremde Adressen und auth/pair werden nicht angesteuert');
    await p.waitForTimeout(300); ok(p.url() === before, 'Adresse unveraendert');
    // Eingabe + Seitenwechsel stoesst widgets an (Seiteninhalt ueber den Live-Kanal)
    await p.waitForTimeout(10500); await clear(p);
    await p.evaluate(() => { document.dispatchEvent(new Event('input')); document.dispatchEvent(new CustomEvent('hk-page-open', { detail: { id: 1 } })); });
    m = await msgs(p);
    ok(m.filter(x => x.action === 'widgets.reload').length === 1, 'Mac: nach Eingabe + Seitenwechsel widgets.reload', m);
    // aus dem iframe (Peek) ans Hauptfenster
    await p.waitForTimeout(10500); await clear(p);
    const fr = await p.evaluate(id => new Promise(r => { const f = document.createElement('iframe'); f.src = '/notes?embed=1#' + id; f.style.width = '300px'; f.onload = () => r(true); document.body.appendChild(f); }), pid);
    await p.waitForTimeout(1500);
    const inner = p.frames().find(f => f !== p.mainFrame() && /embed=1/.test(f.url()));
    if (inner) {
      const r = await inner.evaluate(() => ({ kind: HK.host.kind, has: HK.host.has('widgets'), w: HK.host.widgetsReload(), mine: (window.__hostMsgs || []).length }));
      m = await msgs(p);
      ok(fr && r.has && r.w && r.mine === 0 && m.some(x => x.action === 'widgets.reload'), 'iframe: reicht an das Hauptfenster weiter', { r, m });
    } else ok(false, 'iframe geladen');
    ok(!p.__errs.length, 'Mac: keine Fehler', p.__errs);
    await ctx.close();
  }

  // --- 3. Tauri-Huelle (ohne notify) + spaete Meldung -----------------------------------------------------------------
  {
    const ctx = await ctxFor('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0 SagaApp/0.1 (windows)',
      tauriInit, { info: { kind: 'tauri', version: '0.1', caps: ['badge', 'widgets', 'openExternal'] } });
    const p = await login(ctx, USER, PASS);
    await p.goto(BASE + '/'); await p.waitForTimeout(2500);
    let m = await msgs(p);
    ok(m.length && m.every(x => x.cmd === 'saga_host' && x.msg && x.msg.v === 1), 'Tauri: alles ueber invoke("saga_host", {msg})', m);
    ok(m.some(x => x.msg.action === 'ready') && m.some(x => x.msg.action === 'badge'), 'Tauri: ready + badge', m);
    await clear(p);
    const r = await p.evaluate(() => ({ n: HK.host.notify('x', 'y', '/'), k: HK.host.kind, w: HK.host.widgetsReload(), s: HK.host.has('settings') }));
    m = await msgs(p);
    ok(r.k === 'tauri' && r.n === false && r.w === true && !r.s && m.length === 1 && m[0].msg.action === 'widgets.reload', 'Tauri: nur gemeldete Faehigkeiten (kein notify, keine App-Einstellungen)', { r, m });
    await p.click('#hk-user'); await p.waitForTimeout(300);
    ok(!(await p.$('.menu-item:has-text("App-Einstellungen")')), 'Tauri: kein Menuepunkt "App-Einstellungen" ohne Faehigkeit');
    await p.keyboard.press('Escape');
    ok(!p.__errs.length, 'Tauri: keine Fehler', p.__errs);
    await ctx.close();
    // spaete Meldung per hello
    const ctx2 = await ctxFor(UA_CHROME + ' SagaApp/0.2', tauriInit, null), p2 = await login(ctx2, USER, PASS);
    await p2.goto(BASE + '/notes'); await p2.waitForTimeout(2000);
    let s = await p2.evaluate(() => ({ k: HK.host.kind, c: HK.host.caps.slice(), b: HK.host.badge(4) }));
    ok(s.k === 'tauri' && !s.c.length && s.b === false, 'Tauri ohne Meldung: keine Faehigkeiten', s);
    await clear(p2);
    s = await p2.evaluate(() => { let got = null; sagaHost.on('hello', d => { got = d; }); const r = sagaHost.receive({ type: 'hello', kind: 'tauri', version: '0.2', caps: ['badge', 'notify'] }); return { r, got, k: HK.host.kind }; });
    m = await msgs(p2);
    ok(s.r && s.got && s.got.caps.join() === 'badge,notify' && m.some(x => x.msg.action === 'badge'), 'hello: Faehigkeiten nachgemeldet, Badge nachgereicht', { s, m });
    let ev = await p2.evaluate(() => { const seen = []; sagaHost.on('active', d => seen.push(d.active)); sagaHost.receive({ type: 'active', active: true }); sagaHost.receive({ type: 'weird' }); return seen; });
    ok(ev.join() === 'true', 'Ereignis active an Abonnenten', ev);
    ok(!p2.__errs.length, 'Tauri spaet: keine Fehler', p2.__errs);
    await ctx2.close();
  }

  // --- 4. Alte native App (HlidskjalfApp/1.0, ohne Meldung) -------------------------------------------------------------
  {
    const ctx = await ctxFor(UA_CHROME + ' HlidskjalfApp/1.0', macInit, null), p = await login(ctx, USER, PASS);
    await p.goto(BASE + '/notes'); await p.waitForTimeout(2500);
    const s = await p.evaluate(() => ({ k: HK.host.kind, c: HK.host.caps.join(), m: (window.__hostMsgs || []).slice() }));
    ok(s.k === 'app' && s.c === 'settings,token' && !s.m.length, 'alte App: kind app, nur settings+token, keine neuen Nachrichten', s);
    await p.click('#hk-user'); await p.waitForTimeout(300);
    ok(!!(await p.$('.menu-item:has-text("App-Einstellungen")')), 'alte App: "App-Einstellungen" wie bisher');
    await p.keyboard.press('Escape');
    await p.goto(BASE + '/notes?settings=push'); await p.waitForTimeout(1800);
    const pt = await p.evaluate(() => (document.getElementById('hk-pane-push') || {}).textContent || '');
    ok(!/über die App selbst/.test(pt), 'alte App: Mitteilungs-Einstellungen unveraendert');
    ok(!p.__errs.length, 'alte App: keine Fehler', p.__errs);
    await ctx.close();
  }
  await ctxB.close();

  // --- 5. Offline-Seite -------------------------------------------------------------------------------------------------
  {
    const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'offline.html'), 'utf8');
    const h = x => "'sha256-" + crypto.createHash('sha256').update(x, 'utf8').digest('base64') + "'";
    const st = h(/<style>([\s\S]*?)<\/style>/.exec(html)[1]), sc = h(/<script>([\s\S]*?)<\/script>/.exec(html)[1]);
    const csp = /http-equiv="Content-Security-Policy" content="([^"]+)"/.exec(html)[1];
    ok(csp.indexOf('style-src ' + st) >= 0 && csp.indexOf('script-src ' + sc) >= 0, 'offline.html: CSP-Hashes passen', { style: st, script: sc });
    for (const [loc, scheme, want, dark] of [['de-DE', 'light', 'Keine Verbindung zum Server', false], ['en-US', 'dark', 'No connection to the server', true], ['th-TH', 'light', 'ไม่มีการเชื่อมต่อกับเซิร์ฟเวอร์', false]]) {
      const ctx = await b.newContext({ locale: loc, colorScheme: scheme }), p = await ctx.newPage(), errs = [];
      p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error' || /Content Security Policy|Refused/.test(m.text())) errs.push(m.text()); });
      await p.goto(BASE + '/static/offline.html'); await p.waitForTimeout(400);
      const s = await p.evaluate(() => ({ t: document.getElementById('t').textContent, b: document.getElementById('r').textContent, f: document.activeElement.id,
        bg: getComputedStyle(document.body).backgroundColor, lang: document.documentElement.lang }));
      const rgb = (s.bg.match(/\d+/g) || []).map(Number), isDark = rgb.length >= 3 && rgb[0] + rgb[1] + rgb[2] < 200;
      ok(s.t === want && s.f === 'r' && isDark === dark && !errs.length, 'Offline-Seite ' + loc + '/' + scheme + ': Text, Fokus, Farbe, keine CSP-Fehler', { s, errs });
      await ctx.close();
    }
    // in der Huelle: Erneut versuchen -> {action:'retry'}
    const ctx = await ctxFor(UA_CHROME + ' SagaApp/3.0', macInit, null), p = await ctx.newPage();
    await p.goto(BASE + '/static/offline.html'); await p.click('#r'); await p.waitForTimeout(200);
    const m = await msgs(p);
    ok(m.length === 1 && m[0].action === 'retry', 'Offline-Seite in der Huelle: Erneut versuchen -> retry', m);
    await ctx.close();
  }

  // --- 6. Service Worker: Offline-Seite bei Navigationsfehlern; Rest unveraendert -----------------------------------------
  {
    const ctx = await ctxFor(UA_CHROME + ' Chrome/130.0'), p = await login(ctx, USER, PASS);
    await p.goto(BASE + '/notes'); await p.waitForTimeout(1200);
    const reg = await p.evaluate(() => navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(() => navigator.serviceWorker.ready).then(r => !!r.active).catch(e => String(e)));
    ok(reg === true, 'Service Worker aktiv', reg);
    await p.reload(); await p.waitForTimeout(1500);
    const ctl = await p.evaluate(() => !!navigator.serviceWorker.controller);
    const cached = await p.evaluate(() => caches.keys().then(ks => Promise.all(ks.filter(k => k.indexOf('saga-offline-') === 0).map(k => caches.open(k).then(c => c.match('/static/offline.html')).then(r => !!r)))));
    ok(ctl && cached.length === 1 && cached[0], 'Offline-Seite vorab im Cache (eine Fassung)', { ctl, cached });
    ok(/Seiten|Saga/.test(await p.title()) && !(await p.$('#r')), 'online: Seite normal (nicht die Offline-Seite)');
    // Server-Fehler (404) bleibt Server-Antwort
    const r404 = await p.goto(BASE + '/gibt-es-nicht-bruecke'); ok(r404 && r404.status() === 404 && !(await p.$('main .box #r')), 'HTTP-Fehler bleibt Antwort des Servers', r404 && r404.status());
    await ctx.setOffline(true);
    await p.goto(BASE + '/notes').catch(() => null); await p.waitForTimeout(600);
    const off = await p.evaluate(() => ({ r: !!document.getElementById('r'), u: location.pathname, t: (document.getElementById('t') || {}).textContent })).catch(e => ({ e: String(e) }));
    ok(off.r && off.u === '/notes' && off.t === 'Keine Verbindung zum Server', 'offline: Navigation zeigt die Offline-Seite an Ort und Stelle', off);
    await ctx.setOffline(false);
    await p.click('#r').catch(() => null); await p.waitForTimeout(2500);
    ok(!(await p.$('#r')) && !!(await p.$('#pg-title, #page')), 'Erneut versuchen: Seite wieder da');
    await ctx.close();
  }

  await b.close();
  const bad = res.filter(r => !r[0]);
  console.log(bad.length ? '\n' + bad.length + ' FEHLER von ' + res.length : '\nALLES GRUEN (' + res.length + ')');
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
