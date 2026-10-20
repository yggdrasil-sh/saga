// Browser-Ende-zu-Ende fuer Anrufe 2.4, Etappen E4/E5 (Raum in static/calls.js) - vier Playwright-Kontexte im headless
// Chrome mit Kamera/Mikrofon- und Bildschirm-Attrappe: drei Personen, eine davon mit zweitem Geraet.
//   SAGA_BASE=http://host:port SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... SAGA_USER3=c SAGA_PASS3=... \
//   [SAGA_PID=<pid des Servers fuer die CPU-Messung>] [SHOTS=/pfad] [PLAYWRIGHT=...] [SAGA_CALLS23=calls.js aus v2.3.0] node tests/browser_raum.js
// WebRTC braucht eine Netz-Schnittstelle ausser lo (Chrome sammelt keine Loopback-Kandidaten).
// Geprueft: E4 - 1:1 A <-> B, zweites Geraet von A sieht die Leiste "Anruf laeuft auf …" (Hinzufuegen, Hierher uebernehmen),
// tritt als Begleitgeraet bei (ohne Mikrofon, Wiedergabe stumm, Mesh zu B und zum eigenen Geraet, B bleibt in der
// Einzelansicht), teilt den Bildschirm (B sieht ihn als zweite Kachel), uebernimmt ("Hierher übernehmen"): A verlaesst ohne
// Abbruch, B bleibt verbunden (Tonluecke ueber den Pegel gemessen), Server-Liste [B, A2]. E5 - Gruppenanruf in einer freien
// Gruppe: Knopf im Kopf, klingelt bei B und C, Beitreten, 4er-Mesh (A2 als Begleitgeraet zaehlt mit), je Geraet drei
// Verbindungen, gleiche pc-Kennungen je Paar, alle Fingerabdruecke geprueft, Kacheln, Sprecher-Hervorhebung, Mesh-Bitrate
// (<= 600 kbit/s direkt), Bildschirm teilen in der Gruppe, Verlassen (laeuft weiter), Ende, wenn nur noch eine Person da ist.
// Messung Konzept §8: Aufbau des 4er-Mesh (Klick "Beitreten" bis alle verbunden) und Python-CPU des Servers (Spitze je 0,5 s).
// Kompatibilitaet: B mit dem 2.3-Client (static/calls.js aus v2.3.0) - 1:1 wie bisher, A2 zeigt "braucht Saga 2.4".
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os'), cp = require('child_process');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_USER2 || !E.SAGA_USER3) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2, SAGA_USER3/PASS3 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '';
const res = [], PERF = {};
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, 'raum-' + name + '.png') }); } }
function calls23() {
  if (E.SAGA_CALLS23) return fs.readFileSync(E.SAGA_CALLS23, 'utf8');
  try { return cp.execFileSync('git', ['show', 'v2.3.0:static/calls.js'], { cwd: __dirname, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }); } catch (e) { return ''; }
}
// Pegel je empfangener Tonspur (fuer die Tonluecke bei der Uebernahme): RTCPeerConnection umhuellen (nur im Test)
const PC_HOOK = () => {
  const O = window.RTCPeerConnection; const all = [];
  window.RTCPeerConnection = function (cfg) { const pc = new O(cfg); all.push(pc); return pc; };
  window.RTCPeerConnection.prototype = O.prototype;
  window.__pcs = () => all.filter(x => x.connectionState !== 'closed').length;
  window.__dms = []; window.__ev = [];
  document.addEventListener('hk-live', e => { window.__ev.push(e.detail.type); if (window.__ev.length > 200) window.__ev.shift(); });
  if (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {
    const g = navigator.mediaDevices.getDisplayMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getDisplayMedia = c => g(c).then(s => { window.__dms.push(s); return s; });
  }
};

// Je Geraet ein eigener Browser: Kontexte eines Browsers teilen sich die Verbindungen je Host (HTTP/1.1: 6) - vier
// Live-Kanaele (SSE) plus Anfragen stauen sich sonst, und Ereignisse kommen verspaetet an
const BROWSERS = [];
async function launch() {
  const origin = new URL(BASE).origin;
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin,
           '--auto-select-desktop-capture-source=Entire screen', '--autoplay-policy=no-user-gesture-required'] });
  BROWSERS.push(br);
  return br;
}
async function session(_unused, user, pass, opts = {}) {
  const browser = await launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: 'dark' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  if (opts.js23) await ctx.route('**/static/calls.js*', r => r.fulfill({ status: 200, contentType: 'application/javascript', body: opts.js23 }));
  const p = await ctx.newPage();
  p.__name = opts.name || user;
  p.on('pageerror', e => console.log('  [pageerror ' + p.__name + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + p.__name + '] ' + m.text()));
  await p.addInitScript(PC_HOOK);
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
// Live-Kanal steht (hello empfangen) - verbindet sich erst 2 s nach dem Laden der Seite (static/live.js)
async function liveOk(...ps) { for (const p of ps) await until(() => p.evaluate(() => !!(window.HKLive && window.HKLive.state().sse)), 15000, 200); }
const room = p => p.evaluate(() => window.HKCalls && window.HKCalls.room());
const dev = p => p.evaluate(() => window.HKChat.state().device);
async function until(fn, ms = 20000, step = 100) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const allConn = (r, n) => r && r.links.length === n && r.links.every(l => l.connected) && r;
// Python-CPU des Servers (utime+stime aus /proc) - Spitze je 0,5-s-Fenster und wie lange ueber 20 %
function cpuTicks() { try { const f = fs.readFileSync('/proc/' + E.SAGA_PID + '/stat', 'utf8').split(') ')[1].split(' '); return +f[11] + +f[12]; } catch (e) { return null; } }
function cpuWatch() {
  const hz = 100, out = [], t0 = Date.now(); let last = cpuTicks(), lt = Date.now();
  const iv = setInterval(() => { const c = cpuTicks(), n = Date.now(); if (c != null && last != null) out.push({ t: n - t0, pct: (c - last) / hz / ((n - lt) / 1000) * 100 }); last = c; lt = n; }, 500);
  return () => { clearInterval(iv); const peak = Math.max(0, ...out.map(x => x.pct)); return { peak: Math.round(peak), over20_s: out.filter(x => x.pct > 20).length * 0.5, samples: out.length }; };
}
async function api(p, m, url, body) { return p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, data: e.data, msg: e.message })), [m, url, body]); }

(async () => {
  const browser = null;
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2, U3 = E.SAGA_USER3;
  try {
    const a = await session(browser, U1, E.SAGA_PASS, { name: 'A' }), b = await session(browser, U2, E.SAGA_PASS2, { name: 'B' });
    const c = await session(browser, U3, E.SAGA_PASS3, { name: 'C' });
    await chatKeys(a, U1); await chatKeys(b, U2); await chatKeys(c, U3);
    const a2 = await session(browser, U1, E.SAGA_PASS, { name: 'A2' });          // zweites Geraet von A
    await chatKeys(a2, U1);
    const dA = await dev(a), dA2 = await dev(a2), dB = await dev(b), dC = await dev(c);
    ok('vier Geraete (A, A2 = zweites Geraet von A, B, C) mit eigenen Chat-Schluesseln', dA && dA2 && dB && dC && dA !== dA2, { dA, dA2, dB, dC });

    // --- E4: 1:1, Begleitgeraet, Bildschirm vom Begleitgeraet, Uebernahme -----------------------------------------
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1000);
    await b.goto(BASE + '/chats'); await a2.goto(BASE + '/chats'); await liveOk(a, b, a2);
    await a.click('#ct-call-a');
    await b.waitForSelector('.cl-ring', { timeout: 15000 }); await b.click('.cl-ring .cl-big');
    const r1 = await until(async () => { const r = allConn(await room(a), 1); return r && r.links[0].verified && r; });
    ok('1:1 A <-> B verbunden (Raum mit einer Verbindung, Einzelansicht)', r1 && !r1.mesh && r1.links[0].device === dB && r1.links[0].verified, r1);
    const bar = await until(async () => a2.evaluate(() => { const x = document.querySelector('.cl-run'); return x && x.textContent; }), 10000);
    ok('A2: Leiste "Anruf läuft auf …" mit Hinzufügen und Hierher übernehmen', bar && /Anruf läuft auf/.test(bar) && /Hinzufügen/.test(bar) && /Hierher übernehmen/.test(bar), bar);
    await shot(a2, '1-leiste');
    await a2.click('.cl-run .cl-pill:not(.cl-run-go)');                          // Hinzufuegen (Begleitgeraet)
    const r2 = await until(async () => { const r = allConn(await room(a2), 2); return r && r.links.every(l => l.verified) && r; }, 25000);
    ok('A2: Begleitgeraet - Mikrofon aus, Wiedergabe stumm, verbunden mit B und A', r2 && r2.companion && r2.mic === false && r2.spkOut === false
       && r2.links.map(l => l.device).sort().join() === [dA, dB].sort().join() && r2.links.every(l => l.verified), r2);
    const rb2 = await until(async () => allConn(await room(b), 2), 15000);
    ok('B: zwei Verbindungen (A und Begleitgeraet A2), ohne Klingeln angenommen, Einzelansicht bleibt (A2 still)', rb2 && !rb2.mesh
       && rb2.links.some(l => l.device === dA2 && l.quiet) && !(await b.$('.cl-ring')), rb2);
    const ra2 = await room(a);
    ok('A: Verbindung zum eigenen Begleitgeraet (still, Ton eigener Geraete nie abgespielt)', ra2 && ra2.links.length === 2
       && ra2.links.some(l => l.device === dA2 && l.me && l.quiet), ra2);
    const legs = await api(b, 'GET', '/api/calls/' + r1.id);
    ok('Server-Liste: A (member), B (member), A2 (companion)', legs.ok && legs.r.call.legs.length === 3
       && legs.r.call.legs.some(x => x.device === dA2 && x.role === 'companion'), legs.r && legs.r.call.legs);
    await shot(a2, '2-begleitgeraet');
    // Begleitgeraet teilt den Bildschirm -> B sieht zwei Kacheln (A und Bildschirm von A2)
    await a2.evaluate(() => { try { localStorage.setItem('saga-screen-hint', '0'); } catch (e) { /* egal */ } });
    await a2.click('.cl-ctl .cl-round[aria-label="Bildschirm teilen"]');
    const rbs = await until(async () => { const r = await room(b); return r && r.mesh && r.tiles === 2 && r.links.some(l => l.device === dA2 && l.remote.screen) && r; }, 15000);
    ok('B: Bildschirm des Begleitgeraets als eigene Kachel (Kachelansicht)', rbs, await room(b));
    await shot(b, '3-bildschirm-begleitgeraet');
    await a2.click('.cl-share-stop');
    await until(async () => { const r = await room(b); return r && !r.mesh && r; }, 10000);
    // Uebernahme: A2 -> Hauptgeraet, A verlaesst den Anruf ohne Abbruch
    const lv0 = Date.now();
    const gap = b.evaluate(async (dA2) => {       // laeuft im Hintergrund: Zeit ohne hoerbaren Ton bei B waehrend der Uebernahme
      const t0 = performance.now(); let last = t0, maxGap = 0, fromA2 = 0;
      while (performance.now() - t0 < 9000) {
        const r = window.HKCalls.room(); const lev = r ? Math.max(0, ...r.links.map(l => l.level || 0)) : 0;
        const now = performance.now();
        if (lev > 0.001) { maxGap = Math.max(maxGap, now - last); last = now; }
        if (r && r.links.some(l => l.device === dA2 && l.level > 0.001) && !fromA2) fromA2 = now - t0;
        await new Promise(x => setTimeout(x, 50));
      }
      return { maxGap: Math.round(maxGap), fromA2: Math.round(fromA2) };
    }, dA2);
    await a.evaluate(() => { window.__st = []; const w = document.querySelector('.cl-win .cl-status');
      if (w) new MutationObserver(() => window.__st.push(w.textContent)).observe(w, { childList: true, characterData: true, subtree: true }); });
    await a2.click('.cl-take');
    const gone = await until(async () => { const r = await room(a); return !r && (await a.evaluate(() => (window.__st || []).join(' | '))); }, 15000);
    ok('A: übernommen - Fenster meldet es, A ist nicht mehr im Anruf', gone && /übernommen/.test(gone), gone);
    const r3 = await until(async () => { const r = await room(a2); return r && !r.companion && r.mic && r.spkOut && allConn(r, 1); }, 10000);
    ok('A2: Hauptgeraet (Mikrofon und Ton an), eine Verbindung zu B', r3, await room(a2));
    const rb3 = await until(async () => allConn(await room(b), 1), 10000);
    ok('B: Anruf lief ohne Abbruch weiter, jetzt mit A2', rb3 && rb3.links[0].device === dA2 && rb3.id === r1.id, rb3);
    PERF.uebernahme_ms = Date.now() - lv0;
    const g = await gap;
    PERF.uebernahme_tonluecke_ms = g.maxGap;
    ok('Uebernahme: Tonluecke bei B <= 1000 ms (Pegel; Ton der Attrappe piept, Ziel <= 300 ms)', g.maxGap <= 1000, g);
    const legs3 = await api(b, 'GET', '/api/calls/' + r1.id);
    ok('Server-Liste nach der Uebernahme: B und A2 (member)', legs3.ok && legs3.r.call.legs.length === 2 && legs3.r.call.legs.some(x => x.device === dA2 && x.role === 'member')
       && legs3.r.call.callee_device !== undefined, legs3.r && legs3.r.call.legs);
    await b.click('.cl-ctl .cl-red');
    ok('B legt auf -> Anruf auch auf A2 vorbei', await until(async () => !(await room(a2)), 10000));
    await a.waitForTimeout(800);

    // --- E5: Gruppenanruf, 4er-Mesh -----------------------------------------------------------------------------
    const mk = await api(a, 'POST', '/api/chats', { kind: 'free', members: [U2, U3], device: dA });
    ok('freie Gruppe A, B, C angelegt', mk.ok, mk);
    const gid = mk.r.chat.id;
    for (const p of [a, b, c, a2]) { await p.goto(BASE + '/chats#c' + gid); }
    await a.waitForSelector('#ct-chat:not([hidden])'); await liveOk(a, b, c, a2);
    const hd = await a.evaluate(() => { const v = document.getElementById('ct-call-v'); return { hidden: v.hidden, title: v.title }; });
    ok('Kopf der Gruppe: Knopf "Gruppen-Videoanruf"', !hd.hidden && hd.title === 'Gruppen-Videoanruf', hd);
    const cpu = cpuWatch();
    await a.click('#ct-call-v');
    await b.waitForSelector('.cl-ring', { timeout: 15000 }); await c.waitForSelector('.cl-ring', { timeout: 15000 });
    const ringT = await b.textContent('.cl-ring');
    ok('Gruppenanruf klingelt bei B und C ("Gruppen-Videoanruf …")', /Gruppen-Videoanruf/.test(ringT), ringT);
    await shot(b, '4-gruppe-klingelt');
    let t0 = Date.now();
    await b.click('.cl-ring .cl-big');
    await until(async () => allConn(await room(b), 1), 15000);
    PERF.gruppe_zweiter_beitritt_ms = Date.now() - t0;
    t0 = Date.now();
    await c.click('.cl-ring .cl-big');
    const rc = await until(async () => allConn(await room(c), 2), 15000);
    PERF.gruppe_dritter_beitritt_ms = Date.now() - t0;
    ok('C tritt bei: verbunden mit A und B', rc, await room(c));
    // A2 (Begleitgeraet von A) als viertes Geraet - Leiste in der Gruppe
    await until(async () => a2.evaluate(() => !!document.querySelector('.cl-run')), 10000);
    t0 = Date.now();
    await a2.click('.cl-run .cl-pill:not(.cl-run-go)');
    const r4c = await Promise.all([a, b, c, a2].map(p => until(async () => allConn(await room(p), 3), 15000)));
    PERF.mesh4_aufbau_ms = Date.now() - t0;
    const r4 = await Promise.all([a, b, c, a2].map(p => until(async () => { const r = allConn(await room(p), 3); return r && r.links.every(l => l.verified) && r; }, 8000)));
    const cp4 = cpu();
    PERF.mesh4_python_cpu_spitze_pct = cp4.peak; PERF.mesh4_python_cpu_ueber20_s = cp4.over20_s;
    ok('4er-Mesh: jedes Geraet mit drei Verbindungen, alle verbunden und geprueft', r4c.every(Boolean) && r4.every(r => r && r.links.every(l => l.verified)), r4c.map(r => r && r.links.length));
    ok(`4er-Mesh: Aufbau <= 2 s (${PERF.mesh4_aufbau_ms} ms)`, PERF.mesh4_aufbau_ms <= 2000);
    // 2.4 (Testrunde W10): keine stehengebliebene Zeile "Klingelt bei den anderen …" / "Verbinde …", sobald Medien fliessen
    const lines = await Promise.all([a, b, c, a2].map(p => until(() => p.evaluate(() => {
      const s = document.querySelector('.cl-win .cl-status'); return !s || s.hidden || !s.textContent ? 'leer' : null; }), 5000)));
    const left = await Promise.all([a, b, c, a2].map(p => p.evaluate(() => { const s = document.querySelector('.cl-win .cl-status'); return s && !s.hidden ? s.textContent : ''; })));
    ok('Gruppenanruf: Statuszeile bei allen vier leer, sobald verbunden (W10)', lines.every(Boolean), left);
    ok(`Python-CPU des Servers: Spitze ${cp4.peak} % (Grenze 20 % fuer hoechstens 2 s: ${cp4.over20_s} s)`, !E.SAGA_PID || cp4.over20_s <= 2, cp4);
    const pcs = {};
    r4.forEach((r, i) => r.links.forEach(l => { (pcs[l.pc] = pcs[l.pc] || []).push(i); }));
    ok('je Geraete-Paar eine PeerConnection mit gleicher Kennung auf beiden Seiten (6 Paare)', Object.keys(pcs).length === 6 && Object.values(pcs).every(x => x.length === 2), pcs);
    const rb4 = await room(b);
    ok('B: Kachelansicht mit drei Kacheln', rb4.mesh && rb4.tiles === 3, rb4);
    await b.waitForTimeout(4500);
    const rr = await room(b);
    const cams = rr.links.filter(l => l.rates && l.rates.video);
    ok('Mesh-Bitrate je Strom <= 600 kbit/s (direkt, ab 3 Endpunkten)', cams.length >= 2 && cams.every(l => l.rates.video.maxBitrate <= 600000), cams.map(l => l.rates));
    const spk = await until(async () => (await room(b)).speaker, 8000);
    ok('Sprecher hervorgehoben (Pegel der Tonspur)', !!spk && (await b.$('.cl-tile.speaking')), spk);
    await shot(b, '5-mesh4');
    // Bildschirm teilen in der Gruppe (C)
    await c.evaluate(() => { try { localStorage.setItem('saga-screen-hint', '0'); } catch (e) { /* egal */ } });
    await c.click('.cl-ctl .cl-round[aria-label="Bildschirm teilen"]');
    const scr = await until(async () => {
      const x = await room(b), y = await room(a);
      return x && y && x.links.some(l => l.device === dC && l.remote.screen) && y.links.some(l => l.device === dC && l.remote.screen) && x;
    }, 15000);
    ok('Bildschirm teilen in der Gruppe: A und B sehen den Bildschirm von C', scr);
    await shot(b, '6-gruppe-bildschirm');
    await c.click('.cl-share-stop');
    // Grenze: ein fuenftes Geraet (zweiter Kontext von C) -> voll
    // (Grenze 4 mit Video; der Server prueft - tests/anrufe_raum.py; hier die Anzeige der Leiste)
    // Verlassen: C geht, der Anruf laeuft weiter
    await c.click('.cl-ctl .cl-red');
    const rb5 = await until(async () => allConn(await room(b), 2), 10000);
    ok('C verlaesst: B noch mit A und A2 verbunden, Anruf laeuft weiter', rb5, await room(b));
    const cbar = await until(async () => c.evaluate(() => { const x = document.querySelector('.cl-run'); return x && x.textContent; }), 10000);
    ok('C: Leiste "Gruppenanruf läuft" mit Beitreten', cbar && /Gruppenanruf läuft/.test(cbar) && /Beitreten/.test(cbar), cbar);
    await a2.click('.cl-ctl .cl-red');
    await b.click('.cl-ctl .cl-red');
    ok('B geht, nur noch A (eine Person) -> Anruf endet', await until(async () => !(await room(a)), 10000));
    const msgs = await api(a, 'GET', '/api/chats/' + gid + '/messages?calls=1&device=' + dA);
    const cm = (msgs.r && msgs.r.messages || []).filter(m => m.kind === 'call').pop();
    const cb = cm && (cm.call || JSON.parse(cm.body_ct || '{}'));
    ok('Chat-Eintrag: Gruppenanruf beendet, drei Personen', cb && cb.group && cb.status === 'ended' && cb.n === 3, cb);

    // --- Kompatibilitaet: B mit dem 2.3-Client --------------------------------------------------------------------
    const js23 = calls23();
    if (js23) {
      const b23 = await session(browser, U2, E.SAGA_PASS2, { js23, name: 'B23' });
      await chatKeys(b23, U2);
      await b23.goto(BASE + '/chats'); await liveOk(b23);
      await b.close();
      await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await liveOk(a);
      await a.click('#ct-call-a');
      await b23.waitForSelector('.cl-ring', { timeout: 15000 }); await b23.click('.cl-ring .cl-big');
      const r23 = await until(async () => allConn(await room(a), 1), 20000);
      ok('2.3-Client: 1:1 wie bisher verbunden', r23, await room(a));
      const bar23 = await until(async () => a2.evaluate(() => { const x = document.querySelector('.cl-run'); return x && x.textContent; }), 10000);
      ok('A2: Leiste ohne Knoepfe, Hinweis "braucht Saga 2.4" (Gegenueber ohne multi)', bar23 && /Saga 2\.4/.test(bar23) && !/Hinzufügen/.test(bar23), bar23);
      const g23 = await b23.evaluate(() => { const v = document.getElementById('ct-call-v'); return v ? v.hidden : true; });
      await a.click('.cl-ctl .cl-red');
      await b23.goto('about:blank'); await b23.goto(BASE + '/chats#c' + gid); await b23.waitForSelector('#ct-chat:not([hidden])'); await b23.waitForTimeout(1500);
      ok('2.3-Client: kein Gruppenanruf-Knopf im Gruppen-Chat', await b23.evaluate(() => { const v = document.getElementById('ct-call-v'); return !v || v.hidden; }), g23);
    } else console.log('(2.3-Client nicht verfuegbar - Kompatibilitaet uebersprungen)');
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e));
    for (const br of BROWSERS) for (const ctx of br.contexts()) for (const p of ctx.pages()) {
      const t = await p.evaluate(() => { const s = document.querySelector('.cl-status'); return [location.href, s && s.textContent, JSON.stringify(window.HKLive && window.HKLive.state()), (window.__ev || []).join(','), JSON.stringify(window.HKCalls && window.HKCalls.room())]; }).catch(() => null);
      console.log('  [' + p.__name + '] ' + JSON.stringify(t).slice(0, 1500)); await shot(p, 'fehler-' + p.__name);
    }
  } finally {
    for (const br of BROWSERS) await br.close().catch(() => {});
  }
  console.log('PERF ' + JSON.stringify(PERF));
  console.log(`\nBROWSER-RAUM ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
