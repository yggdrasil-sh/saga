// Browser-Ende-zu-Ende fuer Anrufe (2.1, static/calls.js) - zwei Playwright-Kontexte (zwei Konten) im headless Chrome mit
// Kamera/Mikrofon-Attrappe. Laeuft NICHT im Stack-Testlauf, sondern von einem Rechner mit Playwright gegen einen Test-Stack:
//   SAGA_BASE=http://host:port SAGA_USER=konto-a SAGA_PASS=... SAGA_USER2=konto-b SAGA_PASS2=... [SHOTS=/pfad] \
//   [PLAYWRIGHT=/pfad/zu/node_modules/playwright-core] node tests/browser_calls.js
// Die Konten brauchen nichts Besonderes; Chat-Schluessel werden eingerichtet (Wiederherstellungsschluessel im Temp-Verzeichnis).
// Geprueft: Klingeln auf dem Dashboard, Annehmen oeffnet den Chat-Tab, Verbindung kommt zustande, Medien fliessen in beide
// Richtungen (getStats bytesReceived > 0), Ton kommt an und das <audio> spielt (Video- und Audioanruf, ohne Autoplay-
// Freibrief), DTLS-Fingerabdruck gegen die signierte SDP geprueft, direkter Weg, Stumm/Kamera
// beim Gegenueber sichtbar, Auflegen, Eintrag im Chat, Ablehnen, Handy-Ansicht, verweigertes Mikrofon verstaendlich.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '';
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, name + '.png') }); } }

async function session(browser, user, pass, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1280, height: 800 }, locale: opts.locale || 'de-DE', colorScheme: opts.scheme || 'dark' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.on('pageerror', e => console.log('  [pageerror ' + user + '] ' + e.message));
  if (E.DEBUG) {                                     // DEBUG=1: Konsole und Anruf-Anfragen mitschreiben
    p.on('console', m => console.log('  [console ' + user + '] ' + m.text()));
    p.on('response', r => { if (/\/api\/calls/.test(r.url())) console.log('  [net ' + user + '] ' + r.request().method() + ' ' + r.url().replace(BASE, '') + ' ' + r.status()); });
  }
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
const audioOk = s => !!(s && s.audio && s.audio.packetsIn > 0 && s.audio.bytesOut > 0 && s.audio.playing && s.audio.state === 'ok');
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

(async () => {
  const origin = new URL(BASE).origin;
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin,
           '--auto-select-desktop-capture-source=Entire screen', '--autoplay-policy=document-user-activation-required'] });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2);
    await chatKeys(a, U1); await chatKeys(b, U2);
    ok('Chat-Schluessel in beiden Browsern bereit', !(await a.$('.ct-ov')) && !(await b.$('.ct-ov')));
    // A oeffnet den Direkt-Chat mit B, B wartet auf dem Dashboard
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
    await b.goto(BASE + '/'); await b.waitForTimeout(1500);
    ok('Dashboard von B: Live-Kanal verbunden, Anruf-Skript geladen', await until(() => b.evaluate(() => !!(window.HKCalls && window.HKLive && window.HKLive.state().sse)), 10000),
      await b.evaluate(() => ({ calls: !!window.HKCalls, live: window.HKLive && window.HKLive.state() })));
    ok('Anruf-Knoepfe im Kopf des Direkt-Chats', await a.isVisible('#ct-call-a') && await a.isVisible('#ct-call-v'));
    const csp = await a.evaluate(() => fetch('/chats').then(r => r.headers.get('content-security-policy')));
    ok('CSP unveraendert streng (kein unsafe-inline, nur wasm fuer libsodium)', csp && !/unsafe-inline/.test(csp) && /default-src 'self'/.test(csp), csp);

    // --- 1. Videoanruf: Klingeln auf dem Dashboard, Annehmen oeffnet den Chat-Tab -----------------------------------------
    await a.click('#ct-call-v');
    await b.waitForSelector('.cl-ring', { timeout: 15000 }).catch(() => {});
    ok('Klingeln auf dem Dashboard (anderer Seite)', await b.isVisible('.cl-ring'));
    ok('... mit Name und "Videoanruf"', /Videoanruf/.test(await b.textContent('.cl-ring').catch(() => '')), await b.textContent('.cl-ring').catch(() => ''));
    await b.waitForTimeout(400);
    await shot(b, '01-klingeln-dashboard-dunkel');
    await shot(a, '02-anrufer-klingelt');
    const [bc] = await Promise.all([b.context().waitForEvent('page', { timeout: 15000 }), b.click('.cl-ring .cl-big')]);
    bc.on('pageerror', e => console.log('  [pageerror b-chat] ' + e.message));
    await bc.waitForLoadState('domcontentloaded');
    const conA = await until(async () => { const s = await st(a); return s && s.connected && s; });
    const conB = await until(async () => { const s = await st(bc); return s && s.connected && s; });
    ok('Verbindung kommt zustande (beide Seiten connected)', conA && conB, { conA, conB });
    await a.waitForTimeout(4500);
    const sa = await st(a), sb = await st(bc);
    ok('Medien fliessen: bytesReceived > 0 auf beiden Seiten', sa && sb && sa.bytesIn > 0 && sb.bytesIn > 0, { a: sa && sa.bytesIn, b: sb && sb.bytesIn });
    ok('Ton kommt an und wird abgespielt (Audio-Pakete + <audio> laeuft) auf beiden Seiten', audioOk(sa) && audioOk(sb), { a: sa && sa.audio, b: sb && sb.audio });
    ok('Anruffenster: "Ton: empfangen"', /Ton: empfangen/.test(await a.textContent('.cl-info')) && /Ton: empfangen/.test(await bc.textContent('.cl-info')));
    ok('DTLS-Fingerabdruck gegen die signierte SDP geprueft (verified)', sa && sb && sa.verified && sb.verified);
    ok('Weg: direkt (ohne TURN)', sa && sa.path === 'direct', sa && sa.path);
    ok('Anruffenster zeigt "Ende-zu-Ende verschlüsselt" und "direkt"', /Ende-zu-Ende verschlüsselt/.test(await a.textContent('.cl-info')) && /direkt/.test(await a.textContent('.cl-info')));
    const vid = await bc.evaluate(() => { const v = document.querySelector('.cl-remote'); return v && v.videoWidth; });
    ok('Video des Gegenuebers sichtbar (videoWidth > 0)', vid > 0, vid);
    await shot(a, '03-videoanruf-anrufer');
    await shot(bc, '04-videoanruf-angerufener');
    // Stumm, Kamera aus -> beim Gegenueber sichtbar
    await a.click('.cl-ctl .cl-round >> nth=0');
    ok('Stumm: beim Gegenueber angekommen (signiertes Zustands-Signal)', await until(async () => { const s = await st(bc); return s && s.remote.mic === false; }, 6000));
    await a.click('.cl-ctl .cl-round >> nth=1');
    ok('Kamera aus: Gegenueber zeigt wieder das Profilbild', await until(() => bc.evaluate(() => !document.querySelector('.cl-win').classList.contains('has-video')), 6000));
    await shot(bc, '05-gegenueber-stumm-kamera-aus');
    await a.click('.cl-ctl .cl-round >> nth=1');
    ok('Kamera wieder an: Video beim Gegenueber', await until(() => bc.evaluate(() => document.querySelector('.cl-win').classList.contains('has-video')), 8000));
    // Bildschirm teilen (headless: Quelle automatisch gewaehlt, falls der Browser es kann)
    const canShare = await a.$('.cl-ctl .cl-round >> nth=2');
    if (canShare && (await canShare.getAttribute('aria-label')) === 'Bildschirm teilen') {
      await canShare.click(); await a.waitForTimeout(2000);
      const s = await st(a);
      if (s && s.screen) {
        ok('Bildschirm teilen: Gegenueber bekommt screen=true', await until(async () => { const x = await st(bc); return x && x.remote.screen; }, 6000));
        await shot(bc, '06-bildschirm-geteilt');
        await a.click('.cl-ctl .cl-round >> nth=2');
      } else console.log('--   Bildschirm teilen: im headless Chrome nicht verfuegbar - uebersprungen');
    }
    // Auflegen
    await a.click('.cl-ctl .cl-red');
    ok('Auflegen: Gegenueber sieht das Ende', await until(() => bc.evaluate(() => !window.HKCalls.state()), 8000));
    await a.waitForTimeout(4000);
    await a.waitForSelector('.ct-call', { timeout: 8000 }).catch(() => {});
    const entry = await a.$$eval('.ct-call', l => l.map(x => x.textContent));
    ok('Eintrag im Chat: "Videoanruf · Dauer"', entry.some(x => /Videoanruf · \d+:\d\d/.test(x)), entry);
    await shot(a, '07-chat-mit-anrufeintrag-dunkel');

    // --- 2. Ablehnen (B ist jetzt im Chat-Tab) ---------------------------------------------------------------------------
    await a.click('#ct-call-a');
    await bc.waitForSelector('.cl-ring', { timeout: 15000 }).catch(() => {});
    ok('Klingeln auch im Chat-Tab', await bc.isVisible('.cl-ring'));
    await bc.click('.cl-ring .cl-red');
    ok('Abgelehnt: Anrufer bekommt eine klare Meldung', await until(() => a.evaluate(() => { const s = document.querySelector('.cl-status'); return s && /abgelehnt/.test(s.textContent); }), 8000));
    await shot(a, '08-abgelehnt');
    await a.waitForTimeout(4200);

    // --- 3. Handy-Ansicht + hell: Audioanruf, B auf dem Handy -----------------------------------------------------------
    await bc.setViewportSize({ width: 390, height: 820 }); await bc.emulateMedia({ colorScheme: 'light' });
    await a.click('#ct-call-a');
    await bc.waitForSelector('.cl-ring', { timeout: 15000 }).catch(() => {});
    await bc.waitForTimeout(400);
    await shot(bc, '09-klingeln-handy-hell');
    await bc.click('.cl-ring .cl-big');
    ok('Handy: Audioanruf verbunden', await until(async () => { const s = await st(bc); return s && s.connected; }));
    await bc.waitForTimeout(4500);
    // Audioanruf: das Video-Transceiver bleibt ohne Bild - der Ton darf davon nicht abhaengen (frueher: <audio> blieb stumm)
    const xa = await st(a), xb = await st(bc);
    ok('Audioanruf: Ton in beide Richtungen (Audio-Pakete + <audio> laeuft)', audioOk(xa) && audioOk(xb), { a: xa && xa.audio, b: xb && xb.audio });
    await shot(bc, '10-audioanruf-handy');
    await bc.click('.cl-ctl .cl-red');
    await bc.waitForTimeout(4200);
    await bc.click('.ct-item-b').catch(() => {}); await bc.waitForTimeout(1500);
    await shot(bc, '11-chat-handy-hell');

    // --- 4. Mikrofon verweigert: verstaendliche Meldung ----------------------------------------------------------------
    await a.addInitScript(() => { navigator.mediaDevices.getUserMedia = () => Promise.reject(Object.assign(new Error('denied'), { name: 'NotAllowedError' })); });
    await a.reload(); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);
    await a.click('#ct-call-a');
    const msg = await until(() => a.evaluate(() => { const s = document.querySelector('.cl-status.err'); return s && s.textContent; }), 6000);
    ok('Mikrofon verweigert: Erklaerung statt stillem Fehler', /nicht erlaubt/.test(msg || ''), msg);
    await shot(a, '12-mikrofon-verweigert');
  } catch (e) { console.error(e); res.push(false); }
  await browser.close();
  console.log(`\nBROWSER-ANRUFE ${res.filter(Boolean).length}/${res.length}`, res.every(Boolean) ? '- ALLES GRUEN' : '- FEHLER');
  process.exit(res.every(Boolean) ? 0 : 1);
})();
