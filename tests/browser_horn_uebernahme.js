// Browser-Ende-zu-Ende "Hierher übernehmen" bei einem 1:1-Anruf ÜBER EINEN KNOTEN (calls.horn + calls.horn_all,
// eingebauter oder externer Knoten bereit). Fund: iPhone -> Mac - das neue Gerät meldete das alte nur beim Server ab,
// das alte telefonierte weiter. Geprüft: A ruft B an (Knoten), das zweite Gerät A2 übernimmt aus der Leiste; danach
// ist A2 im Anruf (Ton an, mit dem Knoten verbunden), A hat aufgelegt und B ist noch verbunden - mit A2 statt A.
// 2.5 (Bugs-DB 2198, Feldfall iPhone -> Mac-App): A2 bekommt eine Attrappe fuer navigator.mediaSession.setMicrophoneActive
// wie WebKit - false schaltet die Aufnahme der Seite stumm (Spur muted, auch fuer spaeter geholte Spuren), true nur aus
// einer frischen Nutzergeste (sonst Promise mit InvalidStateError). Geprueft: nach der Uebernahme ist die Aufnahme aktiv
// und B sieht A2 mit Mikrofon an; haengt die Aufnahme trotzdem stumm, zeigen A2 und B ehrlich "stumm", A2 den Hinweis
// "Mikrofon antippen, um es freizugeben" - ein Klick gibt frei. Echtes WebKit fehlt auf der Testmaschine (nur Attrappe).
// BR=firefox: Firefox findet in der Testumgebung (Art knoten) noch keinen Weg zum Knoten (ICE-TCP/TURN-TLS mit Test-CA) -
// bis dahin nur Chromium.
//   SAGA_BASE=... SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... [BR=firefox] node tests/browser_horn_uebernahme.js
'use strict';
const pw = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_USER2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function until(fn, ms = 20000, step = 150) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
let br;
const FF = E.BR === 'firefox';
// WebKit-Verhalten von setMicrophoneActive (MediaSession::updateCaptureState): false -> Seitenaufnahme stumm (Spuren muted +
// Ereignis mute), true ohne Geste -> abgelehnt. "Geste" wie processingUserGestureForMedia: nur synchron in einem
// vertrauenswuerdigen Klick/Tasten-Ereignis (nicht aus Timern - enger als die 5 s von navigator.userActivation).
const WK_MIC = () => {
  const W = window.__wk = { muted: false, calls: [], tracks: [] };
  window.__wkErr = [];
  window.addEventListener('unhandledrejection', e => { const m = String(e.reason && e.reason.message || e.reason); if (/setMicrophoneActive/.test(m)) window.__wkErr.push(m); });
  let inG = false;
  ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click', 'keydown'].forEach(k => window.addEventListener(k, e => {
    if (e.isTrusted) { inG = true; setTimeout(() => { inG = false; }, 0); } }, true));
  const d = Object.getOwnPropertyDescriptor(MediaStreamTrack.prototype, 'muted');
  Object.defineProperty(MediaStreamTrack.prototype, 'muted', { configurable: true, get() { return (this.kind === 'audio' && this.__wk && W.muted) || d.get.call(this); } });
  const setAll = m => { if (W.muted === m) return; W.muted = m; W.tracks.forEach(t => { if (t.readyState === 'live') t.dispatchEvent(new Event(m ? 'mute' : 'unmute')); }); };
  const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = c => gum(c).then(s => { s.getAudioTracks().forEach(t => { t.__wk = true; W.tracks.push(t); }); return s; });
  if (!navigator.mediaSession) return;
  navigator.mediaSession.setMicrophoneActive = function (on) {
    const g = inG;
    W.calls.push((on ? 'true' : 'false') + (on && !g ? '!' : ''));
    if (!on) { setAll(true); return Promise.resolve(); }
    if (!g) return Promise.reject(new DOMException('setMicrophoneActive must be called from a user gesture', 'InvalidStateError'));
    setAll(false); return Promise.resolve();
  };
};
async function session(user, pass, name, wk) {
  const ctx = await br.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE', ignoreHTTPSErrors: FF });
  if (!FF) await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  if (wk) await p.addInitScript(WK_MIC);
  p.on('pageerror', e => console.log('  [pageerror ' + name + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + name + '] ' + m.text()));
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
const horn = p => p.evaluate(() => window.HKCalls && window.HKCalls.horn && window.HKCalls.horn());
const room = p => p.evaluate(() => window.HKCalls && window.HKCalls.room());
const dev = p => p.evaluate(() => window.HKChat.state().device);

(async () => {
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  br = FF ? await pw.firefox.launch({ headless: true, firefoxUserPrefs: { 'media.navigator.permission.disabled': true,
      'media.navigator.streams.fake': true, 'media.autoplay.default': 0, 'network.proxy.type': 0,
      'media.peerconnection.ice.loopback': true, 'media.peerconnection.ice.obfuscate_host_addresses': false } })   // Knoten der Testumgebung auf 127.0.0.1: Firefox sammelt sonst keine Loopback-Kandidaten
    : await pw.chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
      args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] });
  console.log('Browser: ' + (FF ? 'Firefox' : 'Chromium') + ' ' + br.version());
  try {
    const a = await session(U1, E.SAGA_PASS, 'A'), b = await session(U2, E.SAGA_PASS2, 'B');
    await chatKeys(a, U1); await chatKeys(b, U2);
    const a2 = await session(U1, E.SAGA_PASS, 'A2', true);
    await chatKeys(a2, U1);
    const dA = await dev(a), dA2 = await dev(a2);
    ok('zwei Geräte von A und eins von B mit Chat-Schlüsseln', dA && dA2 && dA !== dA2 && await dev(b));

    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1000);
    await b.goto(BASE + '/chats'); await a2.goto(BASE + '/chats'); await a2.waitForTimeout(1500);
    await a.click('#ct-call-v');
    await b.waitForSelector('.cl-ring', { timeout: 20000 }); await b.click('.cl-ring .cl-big');
    const h1 = await until(async () => { const x = await horn(a), y = await horn(b); return x && x.up && y && y.up && [x, y]; }, 30000);
    ok('1:1 A <-> B läuft über den Knoten (beide verbunden)', h1, [await horn(a), await horn(b)]);

    await until(() => a2.evaluate(() => !!document.querySelector('.cl-run .cl-run-go')), 20000, 250);
    ok('A2: Leiste mit "Hierher übernehmen"', await a2.$('.cl-run .cl-run-go'));
    const t0 = Date.now();
    await a2.click('.cl-run .cl-run-go');
    const r2 = await until(async () => { const r = await room(a2), h = await horn(a2); return r && !r.ho && !r.companion && r.mic && h && h.up && r; }, 30000);
    ok(`A2 hat übernommen: Ton an, mit dem Knoten verbunden (${Date.now() - t0} ms)`, r2, [await room(a2), await horn(a2)]);
    const gone = await until(async () => !(await room(a)), 15000, 250);
    ok('A (altes Gerät) hat aufgelegt', gone, await room(a));
    const rb = await until(async () => { const r = await room(b), h = await horn(b); return r && h && h.up && r.legs.includes(dA2) && !r.legs.includes(dA) && r; }, 15000, 250);
    ok('B ist weiter verbunden - mit A2 statt A', rb, await room(b));
    const hint = await a.evaluate(() => document.body.innerText.includes('übernommen'));
    ok('A zeigt "Anruf auf … übernommen"', hint);

    // 2198: Aufnahme von A2 wirklich aktiv (WebKit-Attrappe), B sieht A2 mit Mikrofon an
    const wk = () => a2.evaluate(() => { const C = window.__wk, r = window.HKCalls.room(), mf = document.querySelector('.cl-micfree');
      return { calls: C.calls, pageMuted: C.muted, tracks: C.tracks.filter(t => t.readyState === 'live').map(t => ({ muted: t.muted, enabled: t.enabled })),
               mic: r && r.mic, micOn: r && (r.micOn === undefined ? r.mic : r.micOn), stuck: r && !!r.micStuck, pill: !!(mf && !mf.hidden && mf.offsetParent),
               btn: (document.querySelector('.cl-ctl .cl-round') || {}).getAttribute ? document.querySelector('.cl-ctl .cl-round').getAttribute('aria-label') : '' }; });
    const bMic = () => horn(b).then(h => h && h.st && h.st[dA2] ? h.st[dA2].mic : null);
    const w1 = await until(async () => { const w = await wk(); return w.tracks.length && !w.pageMuted && w.tracks.every(t => !t.muted && t.enabled) && w.micOn && w; }, 8000, 200);
    ok('2198: A2 nimmt nach der Übernahme wirklich auf (Spur nicht muted, kein setMicrophoneActive(false) ohne Geste)', w1, await wk());
    ok('2198: B sieht A2 mit Mikrofon an', await until(async () => (await bMic()) === true, 8000, 200), await horn(b).then(h => h && h.st));

    // Aufnahme haengt trotzdem stumm (WebKit hat sie stummgeschaltet): ehrlich "stumm" bei A2 und B, Hinweis, Klick gibt frei
    await a2.evaluate(() => navigator.mediaSession.setMicrophoneActive(false));
    const w2 = await until(async () => { const w = await wk(); return w.stuck && !w.micOn && w.pill && w; }, 5000, 150);
    ok('2198: hängt die Aufnahme, zeigt A2 "stumm" und den Hinweis "Mikrofon antippen, um es freizugeben"', w2 && /freizugeben/.test(w2.btn), await wk());
    ok('2198: B sieht A2 dann ehrlich als stumm', await until(async () => (await bMic()) === false, 8000, 200), await horn(b).then(h => h && h.st));
    if (E.SHOT) await a2.screenshot({ path: E.SHOT + '-haengt.png' });
    await a2.click('.cl-micfree');
    const w3 = await until(async () => { const w = await wk(); return !w.pageMuted && !w.stuck && w.micOn && !w.pill && w; }, 5000, 150);
    ok('2198: Klick auf den Hinweis gibt die Aufnahme frei (Mikrofon bleibt an)', w3, await wk());
    ok('2198: B sieht A2 wieder mit Mikrofon an', await until(async () => (await bMic()) === true, 8000, 200), await horn(b).then(h => h && h.st));
    if (E.SHOT) await a2.screenshot({ path: E.SHOT + '-frei.png' });
    const errs = await a2.evaluate(() => (window.__wkErr || []).length);
    ok('2198: keine unbehandelte Ablehnung von setMicrophoneActive', errs === 0, errs);
  } catch (e) {
    ok('Ablauf', false, String(e && e.stack || e));
  } finally {
    await br.close();
    const n = res.filter(Boolean).length;
    console.log(`\nHORN-UEBERNAHME ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
    process.exit(n === res.length ? 0 : 1);
  }
})();
