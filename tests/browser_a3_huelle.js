// 2.5 A3 Huelle: Web-Anruf der Mac-Huelle ueber den Medien-Tunnel - Chromium mit Attrappe der Huellen-Bruecke. Laeuft aus
// tests/a3_huelle.py (echte Kette nginx -> saga-media -> Knoten im Tunnel-Betrieb, Go-Stellvertreter e2e/wstunnel).
// Die Attrappe spielt die Huelle: UA "SagaApp/…", window.sagaHostInfo mit Faehigkeit "tunnel", window.webkit.messageHandlers
// hk (Aufzeichner) und hkTunnel (Nachricht mit Antwort wie WKScriptMessageHandlerWithReply): open -> Zugang an wstunnel
// (POST /auth, statt HornTunnel.connect) und {id}, listen -> {port} des wstunnel-Lauschers, close, stats.
// A, B: mit Bruecke (caps ws, Weg ws); C: normaler Browser ohne ws (Gegenprobe S10: Saga schaltet auf direkt, kein Tunnel).
//   SAGA_BASE=… SAGA_USERS=a,b,c SAGA_PASS=… TUN_PORT=… TUN_CTL=h:p HOST_RULES='MAP name 127.0.0.1' node tests/browser_a3_huelle.js
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE, USERS = (E.SAGA_USERS || '').split(',').filter(Boolean), TUN_PORT = +E.TUN_PORT, TUN_CTL = E.TUN_CTL;
if (!BASE || USERS.length < 3 || !E.SAGA_PASS || !TUN_PORT || !TUN_CTL) { console.error('SAGA_BASE, SAGA_USERS (3), SAGA_PASS, TUN_PORT, TUN_CTL setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 4000))); }
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn().catch(() => null); if (v) return v; await sleep(step); } return v; }
const HUES = [0, 120, 240], FREQ = [300, 540, 780], L = 'ABC';
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36';
const CAPS = ['badge', 'widgets', 'openExternal', 'settings', 'token', 'tunnel'];

// Kamera/Mikrofon-Attrappe (Farbe und Ton je Person), wie browser_horn_web.js
const FAKE = ([hue, freq]) => {
  const md = navigator.mediaDevices;
  let ac = null, aStream = null, vTrack = null;
  const video = () => {
    if (vTrack && vTrack.readyState === 'live') return vTrack.clone();
    const cv = document.createElement('canvas'); cv.width = 320; cv.height = 180;
    const g = cv.getContext('2d'); let f = 0;
    setInterval(() => { f++; g.fillStyle = `hsl(${hue} 85% 50%)`; g.fillRect(0, 0, 320, 180); g.fillStyle = '#fff'; g.fillRect((f * 7) % 160, 20, 16, 16); }, 1000 / 15);
    vTrack = cv.captureStream(15).getVideoTracks()[0]; return vTrack;
  };
  const audio = () => {
    if (!ac) { ac = new AudioContext(); const o = ac.createOscillator(); o.frequency.value = freq; const gn = ac.createGain(); gn.gain.value = 0.3; const d = ac.createMediaStreamDestination(); o.connect(gn).connect(d); o.start(); aStream = d.stream; }
    return aStream.getAudioTracks()[0].clone();
  };
  md.getUserMedia = async c => { const s = new MediaStream(); if (c && c.audio) s.addTrack(audio()); if (c && c.video) s.addTrack(video()); return s; };
};
// Huellen-Attrappe (nur mit Bruecke): vor jedem Dokument
const SHELL = (caps) => {
  window.sagaHostInfo = { kind: 'mac', version: '2.5.0', caps };
  window.__hkMsgs = [];
  const reply = m => window.__hkTunnelNative(JSON.stringify(m)).then(r => { const j = JSON.parse(r); if (j.err) throw new Error(j.err); return j.ok; });
  window.webkit = { messageHandlers: { hk: { postMessage: m => { window.__hkMsgs.push(m && m.action); } }, hkTunnel: { postMessage: reply } } };
};
const EYES = () => {
  const cv = document.createElement('canvas'); cv.width = 32; cv.height = 18;
  const g = cv.getContext('2d', { willReadFrequently: true }), out = {};
  document.querySelectorAll('.cl-tile[data-dev]').forEach(t => {
    const v = t.querySelector('video'); if (!v || !v.videoWidth || !t.classList.contains('has-video')) { out[t.dataset.dev] = null; return; }
    g.drawImage(v, 0, 0, 32, 18);
    const [r, gg, b] = g.getImageData(28, 15, 1, 1).data, mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
    if (mx - mn < 30) { out[t.dataset.dev] = -1; return; }
    let h; if (mx === r) h = ((gg - b) / (mx - mn)) % 6; else if (mx === gg) h = (b - r) / (mx - mn) + 2; else h = (r - gg) / (mx - mn) + 4;
    out[t.dataset.dev] = Math.round((h * 60 + 360) % 360);
  });
  return out;
};
const HANG = () => { const b = document.querySelector('.cl-win .cl-round.cl-red'); if (b) b.click(); return !!b; };
const hueOk = (a, b) => a != null && a >= 0 && Math.min(Math.abs(a - b), 360 - Math.abs(a - b)) < 25;

// Bruecke auf der Node-Seite (wie ShellTunnelBridge: nur die eigene Herkunft, open/listen/close/stats, hoechstens 8 offen)
function bridge(page, log) {
  let seq = 0; const open = new Map();
  return async (txt, src) => {
    const m = JSON.parse(txt), o = new URL(src.frame ? src.frame.url() : page.url()).origin;
    log.push(Object.assign({ origin: o, main: !src.frame || src.frame === page.mainFrame() }, m, m.cred ? { cred: '***' } : {}));
    if (o !== new URL(BASE).origin) return JSON.stringify({ err: 'origin' });
    if (m.op === 'open') {
      if (open.size >= 8) return JSON.stringify({ err: 'ws Grenze' });
      if (!/^https:\/\//.test(m.url) || !m.user || !m.cred || !['audio', 'video'].includes(m.role)) return JSON.stringify({ err: 'ws Anfrage' });
      const r = await fetch('http://' + TUN_CTL + '/auth', { method: 'POST', body: m.user + ':' + m.cred }).catch(e => ({ ok: false, status: String(e) }));
      if (!r.ok) return JSON.stringify({ err: 'ws nicht erreichbar' });
      const id = ++seq; open.set(id, { role: m.role, listen: false });
      return JSON.stringify({ ok: { id } });
    }
    if (m.op === 'listen') { const t = open.get(m.id); if (!t) return JSON.stringify({ err: 'ws zu' }); t.listen = true; return JSON.stringify({ ok: { port: TUN_PORT } }); }
    if (m.op === 'close') { open.delete(m.id); return JSON.stringify({ ok: true }); }
    if (m.op === 'stats') return JSON.stringify({ ok: { open: open.size } });
    return JSON.stringify({ err: 'op' });
  };
}

const ARGS = ['--use-fake-ui-for-media-stream', '--ignore-certificate-errors', '--autoplay-policy=no-user-gesture-required', '--no-proxy-server',
  '--host-resolver-rules=' + (E.HOST_RULES || '')];
async function session(i, withShell) {
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true, args: ARGS });
  const ctx = await br.newContext({ viewport: { width: 1100, height: 700 }, locale: 'de-DE', ignoreHTTPSErrors: true, bypassCSP: true,
    userAgent: UA + (withShell ? ' SagaApp/2.5.0 (mac-shell)' : '') });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.__br = br; p.__i = i; p.__log = [];
  p.on('pageerror', e => console.log('  [pageerror ' + L[i] + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + L[i] + '] ' + m.text()));
  if (withShell) {
    const br_ = bridge(p, p.__log);
    await p.exposeBinding('__hkTunnelNative', (src, txt) => br_(txt, src));
    await p.addInitScript(SHELL, CAPS);
  }
  await p.addInitScript(FAKE, [HUES[i], FREQ[i]]);
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', USERS[i]);
  await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(500);
  return p;
}
async function chatKeys(q) {
  await q.goto(BASE + '/chats'); await q.waitForTimeout(2500);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim();
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 90000 });
  }
  await q.waitForTimeout(800);
}
const horn = p => p.evaluate(() => window.HKCalls && window.HKCalls.horn()).catch(() => null);
const dev = p => p.evaluate(() => window.HKChat.state().device);
async function api(p, m, url, body) { return p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, msg: e.message })), [m, url, body]); }
async function tunStats() { return fetch('http://' + TUN_CTL + '/stats').then(r => r.json()).catch(e => ({ err: String(e) })); }

(async () => {
  let rc = 1;
  const ps = [];
  try {
    ps.push(await session(0, true), await session(1, true), await session(2, false));
    const host = await Promise.all(ps.map(p => p.evaluate(() => ({ kind: window.sagaHost.kind, caps: window.sagaHost.caps, tun: !!window.sagaHost.tunnel() }))));
    ok('A/B: Huelle mit Faehigkeit tunnel (sagaHost.tunnel), C: normaler Browser ohne', host[0].kind === 'mac' && host[0].tun && host[1].tun
       && host[2].kind === null && !host[2].tun, host);
    for (const p of ps) await chatKeys(p);
    const devs = []; for (const p of ps) devs.push(await dev(p));
    const hueOf = {}; devs.forEach((d, i) => { hueOf[d] = HUES[i]; });
    const mk = await api(ps[0], 'POST', '/api/chats', { kind: 'free', members: USERS.slice(1), device: devs[0] });
    ok('freie Gruppe mit 3 Mitgliedern', mk.ok, mk);
    const gid = mk.r.chat.id;
    for (const p of ps) await p.goto(BASE + '/chats#c' + gid);
    await ps[0].waitForSelector('#ct-chat:not([hidden])');
    for (let i = 0; i < 3; i++) await api(ps[i], 'PUT', '/api/calls/caps', { device: devs[i], caps: await ps[i].evaluate(() => window.HKCalls.caps()) });
    await until(() => ps[0].evaluate(() => { const v = document.getElementById('ct-call-v'); return v && !v.hidden; }), 15000);
    await ps[0].waitForTimeout(1500);

    // --- A startet: Weg ws ueber die Bruecke -------------------------------------------------------------------------
    let t0 = Date.now();
    await ps[0].click('#ct-call-v');
    const ha = await until(async () => { const h = await horn(ps[0]); return h && h.up && h; }, 30000);
    const msA = Date.now() - t0;
    ok(`A: Anruf ueber den Knoten im Tunnel-Betrieb, Weg ws, beide Leitungen verbunden (${msA} ms)`, ha && ha.path === 'ws' && ha.url === BASE
       && ha.legs.audio && ha.legs.audio.state === 'connected' && ha.legs.video && ha.legs.video.state === 'connected', ha || await horn(ps[0]));
    const la = ps[0].__log;
    const opens = la.filter(m => m.op === 'open'), listens = la.filter(m => m.op === 'listen');
    ok('A: Bruecke je Leitung open (audio, video; url = Saga-Adresse, Zugang nur in der Nachricht) und listen, nur Hauptfenster der eigenen Herkunft',
       opens.length === 2 && opens.map(m => m.role).sort().join() === 'audio,video' && opens.every(m => m.url === BASE && m.user && m.v === 1)
       && listens.length === 2 && la.every(m => m.main && m.origin === new URL(BASE).origin), la);
    ok('A: Reihenfolge open vor listen je Leitung (WS vor dem Lauscher, Vertrag 6/7)', la.findIndex(m => m.op === 'listen') > la.findIndex(m => m.op === 'open'), la.map(m => m.op));

    // --- B tritt bei ---------------------------------------------------------------------------------------------------
    const rang = await ps[1].waitForSelector('.cl-ring', { timeout: 20000 }).then(() => true, () => false);
    if (rang) await ps[1].click('.cl-ring .cl-big'); else await ps[1].evaluate(id => window.HKCalls.join(id, 'member', true), ha.id);
    const hb = await until(async () => { const h = await horn(ps[1]); return h && h.up && h; }, 30000);
    ok('B: beigetreten ueber ws', hb && hb.path === 'ws' && hb.legs.audio.state === 'connected' && hb.legs.video.state === 'connected', hb || await horn(ps[1]));
    const both = await until(async () => { const hs = await Promise.all([horn(ps[0]), horn(ps[1])]); return hs.every(h => h && h.up && h.known.length >= 1 && h.tiles.length >= 1) && hs; }, 30000);
    ok('A/B: Schluessel verteilt, Kachel des anderen', !!both, both || await Promise.all([horn(ps[0]), horn(ps[1])]));
    const seen = await until(async () => {
      const e = await Promise.all([ps[0].evaluate(EYES), ps[1].evaluate(EYES)]);
      return hueOk(e[0][devs[1]], HUES[1]) && hueOk(e[1][devs[0]], HUES[0]) && e;
    }, 40000, 500);
    ok('A sieht B, B sieht A (Farbe = Person) - Bild in beide Richtungen durch den Tunnel', !!seen, seen || await Promise.all([ps[0].evaluate(EYES), ps[1].evaluate(EYES)]));
    // Ton: Tonplaetze mit RTP-Quelle und Paketen
    const heard = await until(async () => {
      const r = await Promise.all([0, 1].map(i => ps[i].evaluate(() => Promise.all([...document.querySelectorAll('.cl-win audio.cl-raudio')].map(a => a.srcObject && a.srcObject.getAudioTracks()[0]))
        .then(() => window.HKCalls.hornStats()).then(s => s && s.rx))));
      return r.every(x => x && Object.keys(x).length) && r;
    }, 20000, 1000).catch(() => null);
    const st = await Promise.all([0, 1].map(i => ps[i].evaluate(() => window.HKCalls.hornStats())));
    const drop = s => { const d = (s && s.drop) || {}; return (d.auth || 0) + (d.format || 0) + (d.codec || 0) + (d.nokey || 0); };
    ok('Frame-E2E unveraendert: entschluesselte Rahmen, keine verworfenen (auth/format/codec/nokey)', st.every(s => s && drop(s) === 0), { st, heard });
    await sleep(3500);
    const da = await horn(ps[0]);
    ok('A: Diagnose transport ws, Weg-Anzeige WS', da && da.diag && da.diag.transport === 'ws' && da.diag.path === 'horn', da && da.diag);
    const ts = await tunStats();
    ok('wstunnel: 4 Tunnel (1 WS je Leitung und Geraet)', ts && (Array.isArray(ts.tunnels) ? ts.tunnels.length : Object.keys(ts.tunnels || ts.conns || {}).length) >= 4, ts);

    // --- C ohne Bruecke (S10): meldet kein ws -> Saga schaltet den Anruf auf direkt, kein Tunnel bei C ---------------------
    const capsC = await ps[2].evaluate(() => window.HKCalls.caps());
    const capsA = await ps[0].evaluate(() => window.HKCalls.caps());
    ok('caps: A (Huelle mit Tunnel) meldet ws, C (Browser) nicht', capsA.includes('ws') && capsA.includes('horn') && !capsC.includes('ws'), { capsA, capsC });
    const closesBefore = ps[0].__log.filter(m => m.op === 'close').length;
    const rc2 = await ps[2].waitForSelector('.cl-ring', { timeout: 20000 }).then(() => true, () => false);
    if (rc2) await ps[2].click('.cl-ring .cl-big'); else await ps[2].evaluate(id => window.HKCalls.join(id, 'member', true), ha.id);
    const room = p => p.evaluate(() => window.HKCalls.room()).catch(() => null);
    const mesh = await until(async () => {
      const r = await Promise.all(ps.map(room));
      return r.every(x => x && !x.horn && x.topo === 'mesh') && r;
    }, 30000, 500);
    ok('C tritt bei: Saga schaltet den Anruf auf direkt (alle drei ohne Knoten, topo mesh)', !!mesh, mesh || (await Promise.all(ps.map(room))).map(x => x && { horn: x.horn, topo: x.topo, direct: x.direct }));
    const connd = await until(async () => {
      const r = await Promise.all(ps.map(room));
      return r.every(x => x && x.links.filter(l => !l.me && l.connected).length >= 2) && r;
    }, 30000, 500);
    ok('direkt: alle drei verbunden', !!connd, connd ? null : { links: (await Promise.all(ps.map(room))).map(x => x && x.links.map(l => [l.name, l.connected, l.established])),
      c: await ps[2].evaluate(() => ({ st: window.HKCalls.state(), room: (({ topo, direct, horn, legs }) => ({ topo, direct, horn, legs }))(window.HKCalls.room() || {}), status: (document.querySelector('.cl-status') || {}).textContent })),
      logA: ps[0].__log.map(m => m.op + ':' + (m.id || m.role)) });
    await until(async () => ps[0].__log.filter(m => m.op === 'close').length >= closesBefore + 2, 10000);
    ok('A: Wechsel auf direkt schliesst beide Tunnel (close je id)', ps[0].__log.filter(m => m.op === 'close').length >= closesBefore + 2
       && !(await ps[2].evaluate(() => !!window.webkit)), ps[0].__log.map(m => m.op + ':' + (m.id || m.role)));
    await ps[0].evaluate(HANG);
    if (E.SHOTS) { fs.mkdirSync(E.SHOTS, { recursive: true }); await ps[1].screenshot({ path: path.join(E.SHOTS, 'a3-huelle-b.png') }); }
    rc = res.every(Boolean) ? 0 : 1;
  } catch (e) {
    console.log('FAIL Ausnahme: ' + (e && e.stack || e));
    res.push(false);
  } finally {
    for (const p of ps) await p.__br.close().catch(() => {});
  }
  console.log(`\nBROWSER-A3-HUELLE ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(rc);
})();
