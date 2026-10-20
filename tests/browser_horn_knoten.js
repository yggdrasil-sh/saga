// Browser-Ende-zu-Ende Gjallarhorn P3 (Web-Minimum in static/calls.js): Gruppenanruf mit 5 Personen ueber den eingebauten
// Knoten, Frame-E2E saga-horn/v1. Laeuft aus tests/anrufe_horn_knoten.py (eigene Instanz mit saga-media + Knoten), geht
// aber auch allein:
//   SAGA_BASE=http://host:port SAGA_USERS=u1,u2,u3,u4,u5 SAGA_PASS=... [PLAYWRIGHT=...] node tests/browser_horn_knoten.js
// Je Person ein eigener Chromium (Live-Kanaele), Kamera und Mikrofon sind Attrappen mit eigener Farbe und eigenem Ton je
// Person (Canvas 640x360 + Oszillator, Lautstaerke aus dem Test steuerbar). Geprueft: Gruppe ueber der Mesh-Grenze laeuft
// ueber den Knoten (topo horn, Ton- und Bild-Leitung ueber ICE-TCP), jedes Geraet verteilt seinen Frame-Schluessel (fkey)
// und bekommt die der anderen, alle sehen alle (Farbe jeder Kachel = Farbe der Person, also richtig entschluesselt und
// zugeordnet) und hoeren alle (nacheinander spricht je eine Person laut: alle anderen hoeren ihre Frequenz, und der Platz
// gehoert laut Knoten dieser Person), Worker-Zaehler (entschluesselte Rahmen, keine Fehler), Verlassen einer Person
// (Neuschluessel, die anderen sehen sich weiter). Messung: Beitritt (Klick bis beide Leitungen verbunden) und bis zum
// ersten Bild. WebRTC braucht eine Netz-Schnittstelle ausser lo.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
const USERS = (E.SAGA_USERS || '').split(',').filter(Boolean);
if (!BASE || USERS.length < 5 || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USERS (5), SAGA_PASS setzen'); process.exit(2); }
const res = [], PERF = {};
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
const HUES = [0, 72, 144, 216, 288], FREQ = [300, 420, 540, 660, 780];
async function until(fn, ms = 20000, step = 200) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

// Kamera/Mikrofon-Attrappe: Farbe und Ton je Person; window.__hornGain(v) aendert die Lautstaerke
const FAKE = ([hue, freq]) => {
  window.__fake = { hue, freq };
  const md = navigator.mediaDevices;
  let ac = null, gain = null, aStream = null, vTrack = null;
  const video = () => {
    if (vTrack && vTrack.readyState === 'live') return vTrack.clone();
    const cv = document.createElement('canvas'); cv.width = 640; cv.height = 360;
    const g = cv.getContext('2d'); let f = 0;
    setInterval(() => {
      f++; g.fillStyle = `hsl(${hue} 85% 50%)`; g.fillRect(0, 0, 640, 360);
      const x = (f * 9) % 400;
      for (let i = 0; i < 48; i++) { const v = (Math.random() * 255) | 0; g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(20 + x + (i % 8) * 20, 20 + ((i / 8) | 0) * 20, 20, 20); }
    }, 1000 / 15);
    vTrack = cv.captureStream(15).getVideoTracks()[0];
    return vTrack;
  };
  const audio = () => {
    if (!ac) {
      ac = new AudioContext(); const o = ac.createOscillator(); o.frequency.value = freq; gain = ac.createGain(); gain.gain.value = 0.02;
      const d = ac.createMediaStreamDestination(); o.connect(gain).connect(d); o.start(); aStream = d.stream;
    }
    return aStream.getAudioTracks()[0].clone();
  };
  window.__hornGain = v => { if (gain) gain.gain.value = v; };
  md.getUserMedia = async c => { const s = new MediaStream(); if (c && c.audio) s.addTrack(audio()); if (c && c.video) s.addTrack(video()); return s; };
};
// Spitzenfrequenz und Pegel je empfangenem Tonplatz (audio[data-slot])
const EAR = () => {
  const ac = window.__earAc || (window.__earAc = new AudioContext());
  window.__ears = window.__ears || {};
  const out = [];
  document.querySelectorAll('.cl-win audio.cl-raudio[data-slot]').forEach(a => {
    const k = a.dataset.slot, s = a.srcObject;
    if (!s) return;
    let e = window.__ears[k];
    if (!e || e.s !== s) { const an = ac.createAnalyser(); an.fftSize = 8192; ac.createMediaStreamSource(s).connect(an); e = window.__ears[k] = { s, an }; }
    const f = new Float32Array(e.an.frequencyBinCount); e.an.getFloatFrequencyData(f);
    let bi = 1; for (let i = 2; i < f.length; i++) if (f[i] > f[bi]) bi = i;
    out.push({ slot: +k, hz: Math.round(bi * ac.sampleRate / e.an.fftSize), db: Math.round(f[bi]) });
  });
  return out;
};
// Farbton jeder Kachel (Mitte rechts unten, ausserhalb des Rauschfelds)
const EYES = () => {
  const cv = document.createElement('canvas'); cv.width = 32; cv.height = 18;
  const g = cv.getContext('2d', { willReadFrequently: true }), out = {};
  document.querySelectorAll('.cl-tile[data-dev]').forEach(t => {
    const v = t.querySelector('video');
    if (!v || !v.videoWidth) { out[t.dataset.dev] = null; return; }
    g.drawImage(v, 0, 0, 32, 18);
    const [r, gg, b] = g.getImageData(28, 15, 1, 1).data, mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
    if (mx - mn < 30) { out[t.dataset.dev] = -1; return; }
    let h; if (mx === r) h = ((gg - b) / (mx - mn)) % 6; else if (mx === gg) h = (b - r) / (mx - mn) + 2; else h = (r - gg) / (mx - mn) + 4;
    out[t.dataset.dev] = Math.round((h * 60 + 360) % 360);
  });
  return out;
};
const hueOk = (a, b) => a != null && a >= 0 && Math.min(Math.abs(a - b), 360 - Math.abs(a - b)) < 25;

const RKF = u => path.join(os.tmpdir(), 'saga-browser-rk-' + BASE.replace(/\W+/g, '_') + '-' + u + '.txt');
async function chatKeys(q) {
  await q.goto(BASE + '/chats'); await q.waitForTimeout(2500);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim(); fs.writeFileSync(RKF(q.__user), rk, { mode: 0o600 });
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 90000 });
  }
  await q.waitForTimeout(800);
}
const BROWSERS = [];
async function session(i) {
  const origin = new URL(BASE).origin;
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true,
    args: [...(E.SAGA_HOST_RULES ? ['--host-resolver-rules=' + E.SAGA_HOST_RULES] : []), '--use-fake-ui-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin, '--ignore-certificate-errors',
           '--autoplay-policy=no-user-gesture-required', '--no-proxy-server', '--disable-features=WebRtcHideLocalIpsWithMdns'] });
  BROWSERS.push(br);
  const ctx = await br.newContext({ viewport: { width: 1100, height: 720 }, locale: 'de-DE', ignoreHTTPSErrors: true });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.__user = USERS[i]; p.__i = i;
  p.on('pageerror', e => console.log('  [pageerror ' + USERS[i] + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + USERS[i] + '] ' + m.text()));
  await p.addInitScript(FAKE, [HUES[i], FREQ[i]]);
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', USERS[i]);
  await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(500);
  return p;
}
const horn = p => p.evaluate(() => window.HKCalls && window.HKCalls.horn());
const stats = p => p.evaluate(() => window.HKCalls && window.HKCalls.hornStats());
const dev = p => p.evaluate(() => window.HKChat.state().device);
async function api(p, m, url, body) { return p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, data: e.data, msg: e.message })), [m, url, body]); }
async function liveOk(ps) { for (const p of ps) await until(() => p.evaluate(() => !!(window.HKLive && window.HKLive.state().sse)), 15000, 200); }

(async () => {
  let rc = 1;
  try {
    const ps = [];
    for (let i = 0; i < 5; i++) ps.push(await session(i));
    for (const p of ps) await chatKeys(p);
    const devs = [];
    for (const p of ps) devs.push(await dev(p));
    ok('5 Personen mit eigenen Chat-Geraeten', devs.every(Boolean) && new Set(devs).size === 5, devs);
    const hueOf = {}; devs.forEach((d, i) => { hueOf[d] = HUES[i]; });
    const mk = await api(ps[0], 'POST', '/api/chats', { kind: 'free', members: USERS.slice(1), device: devs[0] });
    ok('freie Gruppe mit 5 Mitgliedern (ueber der Mesh-Grenze 4)', mk.ok, mk);
    const gid = mk.r.chat.id;
    for (const p of ps) await p.goto(BASE + '/chats#c' + gid);
    await ps[0].waitForSelector('#ct-chat:not([hidden])'); await liveOk(ps);
    // Faehigkeiten wie der Client sie meldet (sonst kann die Meldung beim Laden knapp nach dem Anruf kommen)
    for (let i = 0; i < 5; i++) {
      const caps = await ps[i].evaluate(() => window.HKCalls.caps());
      await api(ps[i], 'PUT', '/api/calls/caps', { device: devs[i], caps });
      if (i === 0) ok('Client meldet horn (Encoded Transform vorhanden, Server-Faehigkeit an): ' + caps.join(','), caps.indexOf('horn') >= 0);
    }
    await until(async () => ps[0].evaluate(() => { const v = document.getElementById('ct-call-v'); return v && !v.hidden && v.title === 'Gruppen-Videoanruf'; }), 10000);
    const cfg = await api(ps[0], 'GET', '/api/calls/config');
    ok('Server meldet horn (Feature-Flag an)', cfg.ok && cfg.r.features.indexOf('horn') >= 0, cfg.r && cfg.r.features);

    // --- Anruf: A startet, B..E treten bei ---------------------------------------------------------------------------
    let t0 = Date.now();
    await ps[0].click('#ct-call-v');
    const ha = await until(async () => { const h = await horn(ps[0]); return h && h.up && h; }, 20000);
    PERF.start_klick_bis_verbunden_ms = Date.now() - t0;
    ok('A: Anruf ueber den Knoten, Ton- und Bild-Leitung verbunden (ICE-TCP)', ha && ha.node === (E.SAGA_NODE || 'localhost') && ha.path === 'tcp'
       && ha.legs.audio.state === 'connected' && ha.legs.video.state === 'connected', ha || await horn(ps[0]));
    ok('A: Frame-E2E-Worker mit Encoded Transform (' + (ha && ha.mode) + ')', ha && (ha.mode === 'script' || ha.mode === 'streams'));
    const joinMs = [];
    for (let i = 1; i < 5; i++) {
      const p = ps[i];
      const rang = await p.waitForSelector('.cl-ring', { timeout: 20000 }).then(() => true, () => false);
      t0 = Date.now();
      if (rang) await p.click('.cl-ring .cl-big');
      else { console.log('  (' + 'ABCDE'[i] + ': kein Klingeln - tritt ueber HKCalls.join bei)'); await p.evaluate(id => window.HKCalls.join(id, 'member', true), ha.id); }
      const h = await until(async () => { const x = await horn(p); return x && x.up && x; }, 25000);
      joinMs.push(Date.now() - t0);
      ok(`${'ABCDE'[i]}: beigetreten, beide Leitungen zum Knoten verbunden`, h && h.path === 'tcp', h || await horn(p));
    }
    PERF.beitritt_klick_bis_verbunden_ms = joinMs;
    const all = await until(async () => {
      const hs = await Promise.all(ps.map(horn));
      return hs.every(h => h && h.up && h.known.length === 4 && h.tiles.length === 4) && hs;
    }, 20000);
    ok('alle 5: verbunden, Schluessel an die 4 anderen verteilt, 4 Kacheln', all, all || (await Promise.all(ps.map(horn))).map(h => h && { up: h.up, known: h.known.length, tiles: h.tiles.length }));

    // --- Sehen: Farbe jeder Kachel = Farbe der Person ------------------------------------------------------------
    t0 = Date.now();
    const seen = await until(async () => {
      const eyes = await Promise.all(ps.map(p => p.evaluate(EYES)));
      const good = eyes.every((e, i) => devs.filter((d, j) => j !== i).every(d => hueOk(e[d], hueOf[d])));
      return good ? eyes : null;
    }, 30000, 500);
    PERF.alle_sehen_alle_ms = Date.now() - t0;
    const eyes = seen || await Promise.all(ps.map(p => p.evaluate(EYES)));
    ok(`alle sehen alle: jede Kachel zeigt die Farbe ihrer Person (entschluesselt, richtig zugeordnet; ${PERF.alle_sehen_alle_ms} ms)`, !!seen,
       eyes.map((e, i) => Object.fromEntries(Object.entries(e).map(([d, h]) => [d.slice(0, 6), [h, hueOf[d]]]))));

    // --- Hoeren: nacheinander spricht je eine Person laut -------------------------------------------------------------
    const heard = [];
    for (let s = 0; s < 5; s++) {
      for (let i = 0; i < 5; i++) await ps[i].evaluate(v => window.__hornGain(v), i === s ? 0.6 : 0.005);
      const got = await until(async () => {
        const r = await Promise.all(ps.map(async (p, i) => {
          if (i === s) return true;
          const ears = await p.evaluate(EAR), h = await horn(p);
          const e = ears.find(x => Math.abs(x.hz - FREQ[s]) < 15 && x.db > -75);
          if (!e) return false;
          const slot = ((h.legs.audio || {}).slots || [])[e.slot] || {};
          return slot.peer && slot.peer === h.hp[devs[s]];
        }));
        return r.every(Boolean) && r;
      }, 12000, 300);
      heard.push(!!got);
      ok(`${'ABCDE'[s]} spricht: alle vier anderen hoeren ${FREQ[s]} Hz, der Tonplatz gehoert laut Knoten ${'ABCDE'[s]}`, !!got,
         await Promise.all(ps.map(async p => { const h = await horn(p); return { ears: await p.evaluate(EAR),
           slots: ((h.legs.audio || {}).slots || []).map(x => Object.keys(h.hp).find(d => h.hp[d] === x.peer) || x.peer).map(d => devs.indexOf(d)) }; })));
    }
    for (const p of ps) await p.evaluate(() => window.__hornGain(0.05));
    // Ruhiger Lauf fuer die CPU-Messung (5 Personen, alle mit Bild und Ton): Zeitfenster fuer tests/anrufe_horn_knoten.py
    const w0 = Date.now() / 1000;
    await ps[0].waitForTimeout(15000);
    console.log('STEADY ' + w0.toFixed(2) + ' ' + (Date.now() / 1000).toFixed(2));

    // --- Worker-Zaehler ----------------------------------------------------------------------------------------------
    const st = await Promise.all(ps.map(stats));
    const sum = (o, re) => Object.keys(o || {}).filter(k => re.test(k)).reduce((a, k) => a + o[k].n, 0);
    const decA = st.map(x => sum(x && x.dec, /^audio/)), decV = st.map(x => sum(x && x.dec, /^video/)), encV = st.map(x => sum(x && x.enc, /^video/));
    ok(`Worker: alle verschluesseln und entschluesseln (Video ver ${encV}, ent ${decV}; Ton ent ${decA})`, decA.every(n => n > 200) && decV.every(n => n > 100) && encV.every(n => n > 100));
    const drops = st.map(x => x && x.drop);
    // auth = Tag falsch (gefaelscht/fremder Schluessel), format = kein Trailer. replay: derselbe Rahmen kurz auf zwei Plaetzen
    // (Platzwechsel Kachel <-> Sprecher) - der zweite wird wie vorgesehen verworfen; nur wenige erlaubt
    const bad = drops.map(d => (d && (d.auth || 0) + (d.format || 0) + (d.codec || 0)) || 0);
    const rep = drops.map((d, i) => (d && d.replay) || 0);
    ok(`Worker: keine gefaelschten oder unverschluesselten Rahmen (auth+format+codec ${bad}), Wiederholungen verworfen (${rep})`,
       bad.every(n => n === 0) && rep.every((n, i) => n <= Math.max(20, decV[i] * 0.02)), drops);
    PERF.worker_drop = drops;
    const perFrame = st.map(x => { const v = x && x.dec && x.dec['video:delta']; return v ? Math.round(v.ms / v.n * 1000) / 1000 : null; });
    PERF.entschluesseln_ms_je_videorahmen = perFrame;

    // --- Verlassen: E geht, Neuschluessel, die anderen sehen sich weiter ----------------------------------------------
    const nBefore = (await Promise.all(ps.slice(0, 4).map(horn))).map(h => h.key_n);
    await ps[4].click('.cl-ctl .cl-red');
    const after = await until(async () => {
      const hs = await Promise.all(ps.slice(0, 4).map(horn));
      return hs.every((h, i) => h && h.tiles.length === 3 && h.known.length === 3 && h.key_n > nBefore[i]) && hs;
    }, 15000);
    ok('E verlaesst: die anderen haben 3 Kacheln und einen neuen Schluessel (Neuschluessel nach Abgang)', after,
       (await Promise.all(ps.slice(0, 4).map(horn))).map(h => h && { tiles: h.tiles.length, known: h.known.length, n: h.key_n }));
    await ps[0].waitForTimeout(3500);                                     // Umschalten nach 1,5 s, alter Schluessel 3 s gueltig
    const eyes2 = await until(async () => {
      const e = await Promise.all(ps.slice(0, 4).map(p => p.evaluate(EYES)));
      return e.every((x, i) => devs.slice(0, 4).filter((d, j) => j !== i).every(d => hueOk(x[d], hueOf[d]))) && e;
    }, 15000, 500);
    ok('nach dem Neuschluessel: die vier sehen sich weiter', !!eyes2);
    if (E.SHOTS) { fs.mkdirSync(E.SHOTS, { recursive: true }); await ps[0].screenshot({ path: path.join(E.SHOTS, 'horn-4.png') }); }
    for (const p of ps.slice(0, 4)) await p.click('.cl-ctl .cl-red').catch(() => {});
    await ps[0].waitForTimeout(1500);
    rc = res.every(Boolean) ? 0 : 1;
  } catch (e) {
    console.log('FAIL Ausnahme: ' + (e && e.stack || e));
    rc = 1;
  } finally {
    for (const b of BROWSERS) await b.close().catch(() => {});
  }
  console.log('PERF ' + JSON.stringify(PERF));
  console.log(`\nBROWSER-HORN-KNOTEN ${res.filter(Boolean).length}/${res.length}` + (rc === 0 ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(rc);
})();
