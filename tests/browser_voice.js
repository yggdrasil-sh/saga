// Browser-Ende-zu-Ende fuer Sprachnachrichten (2.5, static/chat-voice.js + static/chat.js) - zwei Playwright-Kontexte im
// headless Chromium mit unechtem Mikrofon (Pieptoene), Chats der Stufe 3 (tests/browser_v3.py schaltet chat_v3 ein):
//   SAGA_BASE=... SAGA_USER=konto-a SAGA_PASS=... SAGA_USER2=konto-b SAGA_PASS2=... [SHOTS=/pfad] node tests/browser_voice.js
// Geprueft: Mikrofon-Knopf nur bei leerem Feld, Aufnahme (WebCodecs + eigener Ogg-Muxer), Vorschau, Senden (saga-file/v2),
// beim anderen Spieler mit 64 Balken und Dauer aus dur, Abspielen ueber den Service Worker (/_m/), echtes Ogg/Opus mit
// passender Dauer, Tempo 1,5x, Springen, "zu kurz" (< 1 s) und Abbrechen; WebM-Weg (Safari) umgepackt zu Ogg; hell/dunkel,
// Handy-Breite; keine Seitenfehler.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '';
const res = [], errs = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, name + '.png') }); } }
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

async function session(browser, user, pass, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: opts.scheme || 'dark',
                                         isMobile: !!opts.mobile, hasTouch: !!opts.mobile, permissions: ['microphone'] });
  const p = await ctx.newPage();
  p.on('pageerror', e => { errs.push(user + ': ' + e.message); console.log('  [pageerror ' + user + '] ' + e.message); });
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
async function openChat(p) { await p.goto(BASE + '/chats'); await p.waitForTimeout(1200); await p.click('#ct-list .ct-item-b'); await p.waitForSelector('#ct-chat:not([hidden])'); await p.waitForTimeout(1000); }
const voices = p => p.evaluate(() => [...document.querySelectorAll('#ct-msgs .ct-msg .vm')].map(v => ({
  fid: v.dataset.fid, bars: v.querySelectorAll('.vm-wave span').length, time: v.querySelector('.vm-time').textContent,
  hs: [...v.querySelectorAll('.vm-wave span')].map(s => s.style.height), mine: v.closest('.ct-msg').classList.contains('mine'), st: v.vmState() })));
const vstate = (p, i) => p.evaluate(i => { const v = [...document.querySelectorAll('#ct-msgs .ct-msg .vm')][i]; return v ? v.vmState() : null; }, i);
const toastText = p => p.evaluate(() => [...document.querySelectorAll('.toast, .hk-toast')].map(t => t.textContent).join(' | '));

async function recordFor(p, ms) {
  await p.click('#ct-mic');
  await p.waitForSelector('.ct-rec-stop:not([disabled])', { timeout: 15000 });
  await p.waitForTimeout(ms);
  await p.click('.ct-rec-stop');
}

(async () => {
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2);
    await chatKeys(a, U1); await chatKeys(b, U2);
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
    await a.fill('#ct-input', 'Gleich kommt eine Sprachnachricht'); await a.press('#ct-input', 'Enter'); await a.waitForTimeout(800);
    await a.reload(); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);
    const mode = await a.evaluate(() => window.SagaVoice.mode());
    ok('Aufnahmeweg in Chromium: WebCodecs + eigener Ogg-Muxer', mode === 'wc', mode);
    ok('Mikrofon-Knopf sichtbar (leeres Feld)', await until(() => a.isVisible('#ct-mic'), 5000));
    await a.fill('#ct-input', 'x');
    ok('Mikrofon-Knopf weg, sobald Text im Feld steht', !(await a.isVisible('#ct-mic')));
    await a.fill('#ct-input', '');
    // Aufnahme ~3 s
    await a.click('#ct-mic');
    await a.waitForSelector('.ct-rec-stop:not([disabled])', { timeout: 15000 });
    await a.waitForTimeout(3000);
    const t = await a.textContent('#ct-rec-t');
    ok('Aufnahmeleiste: Zeit laeuft, Grenze 15:00', /^0:0[2-4] \/ 15:00$/.test(t), t);
    await shot(a, 'voice-1-aufnahme-dunkel');
    await a.click('.ct-rec-stop');
    await a.waitForSelector('.ct-rec .vm', { timeout: 10000 });
    const pv = await a.evaluate(() => ({ time: document.querySelector('.ct-rec .vm-time').textContent, send: !!document.querySelector('.ct-rec-send') }));
    ok('Vorschau mit Dauer und "Senden"', pv.send && /^0:0[2-4]$/.test(pv.time), pv);
    await shot(a, 'voice-2-vorschau-dunkel');
    await a.click('.ct-rec-send');
    const av = await until(async () => { const v = await voices(a); return v.length ? v : null; }, 20000);
    ok('A: Sprachnachricht im Verlauf (eigene)', av && av[0].mine && av[0].bars === 64, av);
    ok('Aufnahmeleiste wieder weg, Eingabe da', await a.evaluate(() => document.querySelector('#ct-rec').hidden && !document.querySelector('#ct-compose').hidden));
    await openChat(b);
    const bv = await until(async () => { const v = await voices(b); return v.length ? v : null; }, 20000);
    ok('B: Spieler mit 64 Balken, Dauer aus dur', bv && bv[0].bars === 64 && /^0:0[2-4]$/.test(bv[0].time), bv && bv[0]);
    ok('B: Wellenform nicht flach', bv && new Set(bv[0].hs).size > 3, bv && bv[0].hs.slice(0, 10));
    await shot(b, 'voice-3-empfangen-dunkel');
    // Abspielen ueber den Service Worker
    await b.click('#ct-msgs .vm-play');
    const ps = await until(async () => { const s = await vstate(b, 0); return s && !s.paused && s.t > 0.3 ? s : null; }, 15000);
    ok('B: spielt ab (ueber den Service Worker /_m/)', ps && /\/_m\/[0-9a-f]{32}/.test(ps.src), ps || await vstate(b, 0));
    await shot(b, 'voice-4-abspielen-dunkel');
    const info = await b.evaluate(async () => {
      const s = document.querySelector('#ct-msgs .vm').vmState(), buf = await (await fetch(s.src)).arrayBuffer(), h = new Uint8Array(buf.slice(0, 40));
      const ac = new AudioContext(); const ab = await ac.decodeAudioData(buf.slice(0)); ac.close();
      const u8 = new Uint8Array(buf), dv = new DataView(buf); let lp = -1;           // letzte Ogg-Seite: Granule (RFC 7845)
      for (let i = u8.length - 27; i >= 0; i--) if (u8[i] === 0x4f && u8[i + 1] === 0x67 && u8[i + 2] === 0x67 && u8[i + 3] === 0x53) { lp = i; break; }
      const gran = dv.getUint32(lp + 6, true) + dv.getUint32(lp + 10, true) * 4294967296, pre = dv.getUint16(28 + 10, true);
      return { ogg: String.fromCharCode(h[0], h[1], h[2], h[3]), head: String.fromCharCode.apply(null, h.slice(28, 36)), dur: ab.duration, bytes: buf.byteLength, rate: ab.sampleRate, ch: ab.numberOfChannels,
               gdur: (gran - pre) / 48000, fdur: s.dur };
    });
    ok('Datei ist Ogg/Opus (OpusHead), dekodierbar, Dauer passt zu dur, ~24 kbit/s', info.ogg === 'OggS' && info.head === 'OpusHead' && Math.abs(info.dur - 3) < 0.8
       && info.bytes * 8 / info.dur < 40000 && info.ch === 1, info);
    ok('Ogg-Dauer nach RFC 7845: (letzte Granule - pre-skip) / 48000 = dur des Anhangs (auf 0,1 s gerundet)', Math.abs(info.gdur - info.fdur) <= 0.051, info);
    await b.click('#ct-msgs .vm-speed');
    const sp = await vstate(b, 0);
    ok('Tempo 1,5×', sp.rate === 1.5 && (await b.textContent('#ct-msgs .vm-speed')) === '1.5×', sp);
    await b.click('#ct-msgs .vm-play');                    // Pause
    const box = await b.$eval('#ct-msgs .vm-wave', w => { const r = w.getBoundingClientRect(); return { x: r.left, y: r.top + r.height / 2, w: r.width }; });
    await b.mouse.click(box.x + box.w * 0.5, box.y);
    const sk = await until(async () => { const s = await vstate(b, 0); return s && Math.abs(s.t - s.dur / 2) < 0.4 ? s : null; }, 5000);
    ok('Springen ueber die Wellenform (Mitte)', !!sk, await vstate(b, 0));
    await b.click('#ct-msgs .vm-speed'); await b.click('#ct-msgs .vm-speed');     // zurueck auf 1x
    // zu kurz und Abbrechen
    const n0 = (await voices(a)).length;
    await a.click('#ct-mic'); await a.waitForSelector('.ct-rec-stop:not([disabled])', { timeout: 15000 }); await a.waitForTimeout(300); await a.click('.ct-rec-stop');
    const tt = await until(async () => { const x = await toastText(a); return /Zu kurz/.test(x) ? x : null; }, 5000);
    ok('Unter 1 s: verworfen mit Hinweis', !!tt && (await voices(a)).length === n0, tt);
    await a.click('#ct-mic'); await a.waitForSelector('.ct-rec-stop:not([disabled])', { timeout: 15000 }); await a.waitForTimeout(800);
    await a.click('.ct-rec-cancel');
    ok('Abbrechen: Leiste weg, nichts gesendet', await a.evaluate(() => document.querySelector('#ct-rec').hidden) && (await voices(a)).length === n0);
    // RFC 7845 genau (WebCodecs-Weg): letzte Granule - pre-skip = aufgenommene Samples
    const wc = await a.evaluate(async () => {
      const r = await window.SagaVoice.record({ mode: 'wc', maxS: 900 });
      await new Promise(x => setTimeout(x, 1500));
      const o = await r.stop(), buf = await o.blob.arrayBuffer(), u8 = new Uint8Array(buf), dv = new DataView(buf);
      let lp = -1; for (let i = u8.length - 27; i >= 0; i--) if (u8[i] === 0x4f && u8[i + 1] === 0x67 && u8[i + 2] === 0x67 && u8[i + 3] === 0x53) { lp = i; break; }
      const pre = dv.getUint16(38, true), gran = dv.getUint32(lp + 6, true) + dv.getUint32(lp + 10, true) * 4294967296;
      return { pre, gran, dur: o.dur, gdur: (gran - pre) / 48000 };
    });
    ok('WebCodecs-Weg: (Granule - pre-skip) / 48000 = Aufnahmedauer (RFC 7845, Ende abgeschnitten)', wc.pre > 0 && Math.abs(wc.gdur - wc.dur) < 0.0005, wc);
    // WebM-Weg (Safari ohne WebCodecs-Opus): MediaRecorder WebM -> Ogg umgepackt
    const wm = await a.evaluate(async () => {
      const r = await window.SagaVoice.record({ mode: 'mr-webm', maxS: 900 });
      await new Promise(x => setTimeout(x, 2000));
      const o = await r.stop(), buf = await o.blob.arrayBuffer(), h = new Uint8Array(buf.slice(0, 4)), u8 = new Uint8Array(buf), dv = new DataView(buf);
      let lp = -1; for (let i = u8.length - 27; i >= 0; i--) if (u8[i] === 0x4f && u8[i + 1] === 0x67 && u8[i + 2] === 0x67 && u8[i + 3] === 0x53) { lp = i; break; }
      const gdur = (dv.getUint32(lp + 6, true) + dv.getUint32(lp + 10, true) * 4294967296 - dv.getUint16(38, true)) / 48000;
      const ac = new AudioContext(); const ab = await ac.decodeAudioData(buf.slice(0)); ac.close();
      return { ogg: String.fromCharCode.apply(null, h), dur: o.dur, dec: ab.duration, wf: o.wf.length, type: o.blob.type, gdur };
    });
    ok('WebM-Weg: umgepackt zu Ogg, dekodierbar, Dauer stimmt', wm.ogg === 'OggS' && Math.abs(wm.dec - wm.dur) < 0.3 && wm.dur > 1.5 && wm.wf === 64
       && /audio\/ogg/.test(wm.type) && Math.abs(wm.gdur - wm.dur) < 0.0011, wm);
    // Handy (hell): Spieler und Aufnahmeleiste
    const c = b;                                            // dasselbe Konto/Geraet, jetzt hell und schmal
    await c.setViewportSize({ width: 390, height: 844 }); await c.emulateMedia({ colorScheme: 'light' }); await openChat(c);
    await until(async () => (await voices(c)).length, 15000);
    ok('Handy (hell): keine waagrechte Scrollleiste', await c.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    await shot(c, 'voice-5-handy-hell');
    await c.click('#ct-mic'); await c.waitForSelector('.ct-rec-stop:not([disabled])', { timeout: 15000 }); await c.waitForTimeout(1500);
    const rb = await c.$eval('#ct-rec', e => { const r = e.getBoundingClientRect(); return { r: r.right, w: window.innerWidth }; });
    ok('Handy: Aufnahmeleiste passt in die Breite', rb.r <= rb.w + 1, rb);
    await shot(c, 'voice-6-handy-aufnahme-hell');
    await c.click('.ct-rec-stop'); await c.waitForSelector('.ct-rec .vm');
    await shot(c, 'voice-7-handy-vorschau-hell');
    await c.click('.ct-rec-del');
    const d = a;
    await d.emulateMedia({ colorScheme: 'light' }); await openChat(d);
    await until(async () => (await voices(d)).length, 15000);
    await shot(d, 'voice-8-desktop-hell');
    ok('keine Seitenfehler', errs.length === 0, errs);
  } catch (e) { console.log('FAIL Ausnahme: ' + (e && e.stack || e)); res.push(false); }
  await browser.close();
  const n = res.filter(Boolean).length;
  console.log('\nBROWSER-VOICE ' + n + '/' + res.length + ' - ' + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
