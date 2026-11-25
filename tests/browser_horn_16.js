// 2.6 (Bugs-DB 2263, Schritt 2263-5): Knoten-Anruf mit 16 Teilnehmenden im Web und reservierte Signal-Typen. Laeuft aus
// tests/anrufe_horn_web.py (Teil t2263; HORN_WEB_NUR=2263 nur dieser Teil): Python legt 15 Personen an (Attrappen ohne
// Medien, Beitritt ueber die API), dieser Browser ist die 16. Person. Absprache ueber stdin/stdout, je eine Zeile:
//   Browser -> Python  "BEREIT <chat-geraet>"        angemeldet, Chat-Schluessel da
//   Python -> Browser  "CALL <call_id> <limit>"      Anruf laeuft, 15 Endpunkte sind drin
//   Browser -> Python  "DRIN"                        beigetreten, Ansicht geprueft, Bilder gemacht
//   Python -> Browser  "STILL <dev1> <dev2>"         reservierte dtmf/svc-join (versiegelt mit seal_for) sind zugestellt
//   Browser -> Python  "GEPRUEFT"                    stille Runde geprueft (Python prueft danach die Antworten des Browsers)
//   Python -> Browser  "KONTROLLE <dev1> <dev3>"     state mic:false von dev1, state-Vektor (mic an) von dev3
//   Browser -> Python  "FERTIG"
// Geprueft: keine Marke "Experimentell" im Kopf und kein Satz in der Teilnehmerliste; 16 Kacheln (15 Gegenueber + eigene),
// Zaehler "16/16" (Grenze vom Server, Admin hat auf 16 gesenkt), Teilnehmerliste mit 16 Eintraegen, rollbar, kein
// waagrechtes Scrollen bei 1280 und 390 px; nach dtmf/svc-join: kein Zustand (H.st), keine Kennungen, keine Kachel stumm,
// kein Toast, kein Seitenfehler, Anruf und Endpunkte unveraendert; Kontrollfaelle wirken (stumm-Markierung, Zustand).
// Bilder <PRE>-16-*.png nach SHOTS. HX_VORHER=1: nur Bilder (Stand vor 2263), Absprache wie oben, keine Pruefung.
//   SAGA_BASE SAGA_USER SAGA_PASS SHOTS [HX_PREFIX=nachher] [HX_VORHER=1] [BR=firefox] [PLAYWRIGHT=...]
'use strict';
const pw = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os'), readline = require('readline');
const E = process.env, BASE = E.SAGA_BASE, SHOTS = E.SHOTS || '/tmp', VORHER = E.HX_VORHER === '1', FF = E.BR === 'firefox';
const PRE = (E.HX_PREFIX || (VORHER ? 'vorher' : 'nachher')) + '-' + (FF ? 'firefox' : 'chromium');
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { if (VORHER) return; res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function until(fn, ms = 20000, step = 200) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const sleep = ms => new Promise(r => setTimeout(r, ms));
const shots = [];
async function shot(p, name) { const f = path.join(SHOTS, PRE + '-16-' + name + '.png'); await p.screenshot({ path: f }); shots.push(path.basename(f)); }
// Zeilen von stdin der Reihe nach
const lines = [], waiters = [];
readline.createInterface({ input: process.stdin }).on('line', l => { const w = waiters.shift(); if (w) w(l.trim()); else lines.push(l.trim()); });
function next(ms = 240000) { return lines.length ? Promise.resolve(lines.shift()) : new Promise((ok_, bad) => { const t = setTimeout(() => bad(new Error('keine Zeile von Python')), ms); waiters.push(l => { clearTimeout(t); ok_(l); }); }); }
function say(l) { process.stdout.write(l + '\n'); }

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
const horn = p => p.evaluate(() => window.HKCalls && window.HKCalls.horn && window.HKCalls.horn());
// Lage im Anruffenster: Kopf, Kacheln, Zaehler, Marke, Teilnehmerliste
const VIEW = () => {
  const w = document.querySelector('.cl-win'), tiles = [...document.querySelectorAll('.cl-win .cl-grid .cl-tile')];
  const vis = tiles.filter(t => { const r = t.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  const rs = vis.map(t => t.getBoundingClientRect()), rows = new Set(rs.map(r => Math.round(r.top))).size, cols = new Set(rs.map(r => Math.round(r.left))).size;
  const inView = rs.filter(r => r.left >= -1 && r.right <= innerWidth + 1 && r.top >= -1 && r.bottom <= innerHeight + 1).length;
  const cnt = w && w.querySelector('.cl-info .cl-count'), pl = w && w.querySelector('.cl-plist'), ul = pl && pl.querySelector('.cl-plist-l:not(.cl-away-l)');
  const labs = vis.map(t => { const b = t.querySelector('.cl-tl b'); return b ? b.scrollWidth <= b.clientWidth + 1 || getComputedStyle(b).textOverflow === 'ellipsis' : true; });
  const names = vis.filter(t => t.dataset.dev).map(t => t.querySelector('.cl-tl b')).filter(Boolean).map(b => Math.round(b.getBoundingClientRect().width));
  return { tiles: tiles.length, vis: vis.length, rows, cols, inView, minW: rs.length ? Math.round(Math.min(...rs.map(r => r.width))) : 0,
           muted: tiles.filter(t => t.classList.contains('muted')).map(t => t.dataset.dev),
           count: cnt ? cnt.textContent.trim() : '', countTitle: cnt ? cnt.title : '',
           exp: document.querySelectorAll('.cl-exp, .cl-pl-exp').length, expText: /experimentell/i.test(w ? w.textContent : ''),
           plist: !!pl, plN: ul ? ul.children.length : 0, plHead: pl ? (pl.querySelector('.cl-plist-h b') || {}).textContent : '',
           plScroll: ul ? getComputedStyle(ul).overflowY : '', plFits: pl ? pl.getBoundingClientRect().bottom <= innerHeight + 1 && pl.getBoundingClientRect().right <= innerWidth + 1 : null,
           labelsOk: labs.every(Boolean), minName: names.length ? Math.min(...names) : 0, ow: document.documentElement.scrollWidth - innerWidth, vw: innerWidth };
};
async function showBars(p) { await p.mouse.move(200 + Math.random() * 50, 150); await sleep(250); }
async function plist(p, open) {
  const isOpen = await p.evaluate(() => !!document.querySelector('.cl-win .cl-plist'));
  if (isOpen === open) return;
  await showBars(p);
  if (await p.$('.cl-ctl .cl-people:visible')) await p.click('.cl-ctl .cl-people');
  else { await p.click('.cl-ctl .cl-more'); await p.click('.cl-moremenu .cl-people'); }
  await until(() => p.evaluate(o => !!document.querySelector('.cl-win .cl-plist') === o, open), 4000);
  await sleep(400);
}

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const br = FF
    ? await pw.firefox.launch({ headless: true, firefoxUserPrefs: { 'media.navigator.permission.disabled': true, 'media.navigator.streams.fake': true,
        'media.autoplay.default': 0, 'network.proxy.type': 0, 'media.peerconnection.ice.loopback': true, 'media.peerconnection.ice.obfuscate_host_addresses': false } })
    : await pw.chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true,
        args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required', '--ignore-certificate-errors', '--no-proxy-server'] });
  console.log('Browser: ' + (FF ? 'Firefox' : 'Chromium') + ' ' + br.version() + (VORHER ? ' (VORHER: nur Bilder)' : ''));
  let p;
  try {
    const ctx = await br.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: 'dark', ignoreHTTPSErrors: true });
    if (!FF) await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
    p = await ctx.newPage(); p.__errs = [];
    p.on('pageerror', e => { p.__errs.push(e.message); console.log('  [pageerror] ' + e.message); });
    if (E.DEBUG) p.on('console', m => console.log('  [console] ' + m.text()));
    // Toasts mitschreiben (HK.toast wird bei jedem Aufruf ueber HK gelesen)
    await p.addInitScript(() => {
      window.__toasts = [];
      const hook = () => { if (window.HK && window.HK.toast && !window.HK.toast.__w) { const o = window.HK.toast; window.HK.toast = function (m) { window.__toasts.push(String(m)); return o.apply(this, arguments); }; window.HK.toast.__w = true; } };
      document.addEventListener('DOMContentLoaded', hook); setInterval(hook, 200);
      try { localStorage.setItem('saga-call-layout', 'gallery'); } catch (e) { /* egal */ }
    });
    await p.goto(BASE + '/login');
    await p.fill('input[name=username], input#username, input[type=text]', E.SAGA_USER);
    await p.fill('input[type=password]', E.SAGA_PASS);
    await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
    await p.waitForTimeout(500);
    await p.evaluate(() => fetch('/api/first-steps', { method: 'POST', credentials: 'same-origin', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer: 'no' }) })).catch(() => {});
    await chatKeys(p, E.SAGA_USER);
    const dev = await until(() => p.evaluate(() => window.HKChat && window.HKChat.state().device), 20000);
    ok('angemeldet, Chat-Geraet da', !!dev, dev);
    say('BEREIT ' + (dev || ''));

    const [w0, cid, limS] = (await next()).split(' '), LIM = +limS || 16;
    if (w0 !== 'CALL') throw new Error('erwartet CALL, kam ' + w0);
    await p.evaluate(id => window.HKCalls.join(id, 'member', true), cid);
    const up = await until(async () => { const h = await horn(p); return h && h.up && h; }, 45000, 300);
    const h0 = up || await horn(p);
    ok(`beigetreten ueber den Knoten (${FF ? 'Firefox' : 'Chromium'}), Weg ${h0 && h0.path}`, !!up, h0 && { up: h0.up, path: h0.path, errs: h0.errs, log: (h0.log || []).slice(-8) });
    await until(async () => (await p.evaluate(VIEW)).tiles >= LIM, 15000);
    await sleep(2500); await showBars(p);
    const v = await p.evaluate(VIEW);
    console.log('  Ansicht 1280: ' + JSON.stringify(v));
    const ht = (await horn(p)).tiles.length;
    ok(`1280: ${LIM} Kacheln (${ht} Gegenueber + eigene), alle sichtbar und im Fenster, ${v.cols} Spalten x ${v.rows} Reihen`,
       v.tiles === LIM && ht === LIM - 1 && v.vis === LIM && v.inView === LIM && v.cols >= 4 && v.rows >= 3 && v.minW >= 160, Object.assign({ ht }, v));
    ok(`1280: Zaehler "${LIM}/${LIM}" (Grenze vom Server), Tooltip Geraete`, v.count === LIM + '/' + LIM && v.countTitle.includes(LIM + '/' + LIM), v);
    ok('1280: keine Marke "Experimentell", kein Satz, kein waagrechtes Scrollen, Namen gekuerzt statt uebergelaufen', v.exp === 0 && !v.expText && v.ow <= 1 && v.labelsOk, v);
    await shot(p, 'galerie-1280-dunkel');
    await plist(p, true);
    const vl = await p.evaluate(VIEW);
    console.log('  Liste 1280: ' + JSON.stringify(vl));
    ok(`1280: Teilnehmerliste mit ${LIM} Eintraegen ("Teilnehmende (${LIM})"), rollbar, im Fenster, ohne Satz`,
       vl.plN === LIM && vl.plHead.includes('(' + LIM + ')') && vl.plScroll === 'auto' && vl.plFits && vl.exp === 0 && !vl.expText, vl);
    await shot(p, 'liste-1280-dunkel');
    await p.evaluate(() => { const ul = document.querySelector('.cl-win .cl-plist .cl-plist-l'); if (ul) ul.scrollTop = ul.scrollHeight; });
    await sleep(300); await shot(p, 'liste-1280-unten-dunkel');
    await plist(p, false);
    await p.emulateMedia({ colorScheme: 'light' }); await sleep(300); await showBars(p); await shot(p, 'galerie-1280-hell'); await p.emulateMedia({ colorScheme: 'dark' });
    // schmal
    await p.setViewportSize({ width: 390, height: 844 }); await sleep(1200); await showBars(p);
    const vs = await p.evaluate(VIEW);
    console.log('  Ansicht 390: ' + JSON.stringify(vs));
    ok(`390: ${LIM} Kacheln, kein waagrechtes Scrollen, Zaehler ${LIM}/${LIM}, keine Marke`, vs.tiles === LIM && vs.ow <= 1 && vs.count === LIM + '/' + LIM && vs.exp === 0, vs);
    ok(`390: Namen in den schmalen Kacheln lesbar (Geraetename schrumpft zuerst; schmalster Name ${vs.minName} px >= 36)`, vs.minName >= 36, vs);
    await shot(p, 'galerie-390-dunkel');
    await plist(p, true);
    const vsl = await p.evaluate(VIEW);
    ok(`390: Teilnehmerliste mit ${LIM} Eintraegen, rollbar, ohne waagrechtes Scrollen`, vsl.plN === LIM && vsl.plScroll === 'auto' && vsl.ow <= 1 && vsl.exp === 0, vsl);
    await shot(p, 'liste-390-dunkel');
    await plist(p, false);
    await p.setViewportSize({ width: 1280, height: 800 }); await sleep(800);

    // --- reservierte Typen: still ---------------------------------------------------------------------------------------
    const before = await horn(p), toasts0 = await p.evaluate(() => window.__toasts.length), errs0 = p.__errs.length;
    say('DRIN');
    const [w1, d1, d2] = (await next()).split(' ');
    if (w1 !== 'STILL') throw new Error('erwartet STILL, kam ' + w1);
    await sleep(3500);
    const after = await horn(p), va = await p.evaluate(VIEW), toasts = await p.evaluate(() => window.__toasts.slice());
    const hpx = await p.evaluate(() => { const h = window.HKCalls.horn(); return h && h.hp; });
    ok('dtmf/svc-join: kein Zustand der Absender (H.st), auch nicht aus Feldern wie mic/cam/ready/replaces',
       after && !after.st[d1] && !after.st[d2] && JSON.stringify(Object.keys(after.st).sort()) === JSON.stringify(Object.keys(before.st).sort()), after && after.st);
    ok('dtmf/svc-join: keine Kachel stumm, keine Anzeige, kein Toast, kein Seitenfehler',
       va.muted.length === 0 && toasts.length === toasts0 && p.__errs.length === errs0 && va.exp === 0, { muted: va.muted, toasts, errs: p.__errs });
    await showBars(p); await shot(p, 'galerie-1280-nach-dtmf');
    ok('dtmf/svc-join: Anruf laeuft weiter (Knoten verbunden, kein Wechsel, eigener Schluessel unveraendert, alle Endpunkte da)',
       after && after.up && JSON.stringify(after.switches) === JSON.stringify(before.switches) && after.key_n === before.key_n && after.tiles.length === LIM - 1 && !after.switching
       && JSON.stringify(hpx) === JSON.stringify(before.hp) && (after.errs || []).length === (before.errs || []).length && va.tiles === LIM,
       after && { up: after.up, sw: [before.switches, after.switches], key: [before.key_n, after.key_n], errs: after.errs });
    // --- Kontrollfaelle: derselbe Weg wertet bekannte Typen aus ------------------------------------------------------------
    say('GEPRUEFT');
    const [w2, k1, k3] = (await next()).split(' ');
    if (w2 !== 'KONTROLLE') throw new Error('erwartet KONTROLLE, kam ' + w2);
    const ctl = await until(async () => { const h = await horn(p); return h && h.st[k1] && h.st[k1].mic === false && h.st[k3] && h.st[k3].mic === true && h; }, 10000);
    await sleep(500);
    const vc = await p.evaluate(VIEW);
    ok('Kontrolle: state mic:false wirkt (Zustand, Kachel stumm), state-Vektor (mic an) wird uebernommen',
       !!ctl && vc.muted.indexOf(k1) >= 0 && vc.muted.length === 1, { st: ctl && ctl.st, muted: vc.muted });
    await showBars(p); await shot(p, 'galerie-1280-kontrolle');
    ok('keine Seitenfehler', p.__errs.length === 0, p.__errs);
  } catch (e) {
    if (VORHER) console.log('Ablauf: ' + String(e && e.stack || e)); else ok('Ablauf', false, String(e && e.stack || e));
    if (p) { try { console.log('  horn: ' + JSON.stringify(await horn(p)).slice(0, 3000)); } catch (e2) { /* egal */ } }
  } finally {
    say('FERTIG');
    await br.close();
    console.log('Bilder: ' + shots.join(' '));
    if (VORHER) { console.log(`\nHORN-16 Bilder ${shots.length} (vorher, ohne Pruefung)`); process.exit(0); }
    const n = res.filter(Boolean).length;
    console.log(`\nHORN-16 Browser ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
    process.exit(n === res.length ? 0 : 1);
  }
})();
