// Horn-Probe: Messablauf nach Konzept §5.2/§5.3.
// Grundmessung (WSS-Ping) -> je Weg (UDP, ICE-TCP, TURN-TLS) Profil "12 Video"
// -> "25 Ton" auf dem besten TCP-Weg. Speichert nur Messwerte, keine Adressen.
'use strict';

const Q = new URLSearchParams(location.search);
const DUR = +(Q.get('dur') || 60);   // Sekunden je Weg
const BASE = +(Q.get('base') || 30); // Grundmessung
const TON = +(Q.get('ton') || 30);   // 25 Ton
const WARM = Math.min(5, Math.max(1, Math.round(DUR / 6))); // Anlauf ohne Wertung
const TIMEOUT = { udp: 8000, tcp: 8000, turn: 15000 };
const PROFILES = {
  v12: { label: '12 Video', n: 12, audio: 3, video: 11 }, // 3 Ton + Sprecher + (N-2) Kacheln
  a25: { label: '25 Ton', n: 25, audio: 3, video: 0 },    // Top-3-Ton
};
const PATHS = { udp: 'UDP', tcp: 'ICE-TCP', turn: 'TURN-TLS' };

const $ = (id) => document.getElementById(id);
let session = null;
let worker = null;
const useScript = typeof window.RTCRtpScriptTransform === 'function';

// ---------- Hilfen ----------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rid = () => Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, '0')).join('');
function pct(arr, p) {
  if (!arr.length) return null;
  const a = [...arr].sort((x, y) => x - y);
  return a[Math.min(a.length - 1, Math.floor(p * (a.length - 1) + 0.5))];
}
const r1 = (x) => (x == null || !isFinite(x) ? null : Math.round(x * 10) / 10);
const r2 = (x) => (x == null || !isFinite(x) ? null : Math.round(x * 100) / 100);

function browserInfo() {
  const ua = navigator.userAgent;
  let name = 'Unbekannt', ver = '';
  let m;
  if ((m = ua.match(/Edg\/(\d+)/))) { name = 'Edge'; ver = m[1]; }
  else if ((m = ua.match(/Firefox\/(\d+)/))) { name = 'Firefox'; ver = m[1]; }
  else if ((m = ua.match(/(?:Chrome|CriOS)\/(\d+)/))) { name = /HeadlessChrome/.test(ua) ? 'Chromium (headless)' : 'Chrome'; ver = m[1]; }
  else if ((m = ua.match(/Version\/(\d+(?:\.\d+)?).*Safari/))) { name = 'Safari'; ver = m[1]; }
  let os = 'Unbekannt';
  if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) os = 'iOS';
  else if (/Android/.test(ua)) os = 'Android';
  else if (/Mac OS X/.test(ua)) os = 'macOS';
  else if (/Windows/.test(ua)) os = 'Windows';
  else if (/Linux/.test(ua)) os = 'Linux';
  return { browser: name, version: ver, os };
}

// ---------- synthetische Quellen ----------
let srcVideo = null, srcAudio = null, drawTimer = null, audioCtx = null;
function startSources() {
  const cv = $('src');
  const g = cv.getContext('2d');
  let f = 0;
  const draw = () => {
    f++;
    const w = cv.width, h = cv.height;
    g.fillStyle = `hsl(${(f * 2) % 360} 45% 35%)`;
    g.fillRect(0, 0, w, h);
    // bewegte Flaechen + Rauschbloecke, damit der Encoder echte Arbeit hat
    for (let i = 0; i < 6; i++) {
      g.fillStyle = `hsl(${(i * 60 + f) % 360} 70% 55%)`;
      const x = (Math.sin((f + i * 40) / 30) * 0.4 + 0.5) * (w - 160);
      const y = (Math.cos((f + i * 25) / 23) * 0.4 + 0.5) * (h - 120);
      g.fillRect(x, y, 160, 120);
    }
    for (let i = 0; i < 60; i++) {
      const v = (Math.random() * 255) | 0;
      g.fillStyle = `rgb(${v},${(v * 7) & 255},${(v * 13) & 255})`;
      g.fillRect(Math.random() * w, Math.random() * h, 24, 24);
    }
    g.fillStyle = '#fff';
    g.font = '48px system-ui, sans-serif';
    g.fillText('Horn-Probe ' + f, 32, h - 40);
  };
  draw();
  drawTimer = setInterval(draw, 1000 / 30);
  srcVideo = cv.captureStream(30).getVideoTracks()[0];
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const osc = audioCtx.createOscillator();
  const lfo = audioCtx.createOscillator();
  const lfoGain = audioCtx.createGain();
  const gain = audioCtx.createGain();
  osc.frequency.value = 330;
  lfo.frequency.value = 0.7;
  lfoGain.gain.value = 80; // Tonhoehe wandert, damit DTX nicht alles stilllegt
  gain.gain.value = 0.2;
  lfo.connect(lfoGain).connect(osc.frequency);
  const dst = audioCtx.createMediaStreamDestination();
  osc.connect(gain).connect(dst);
  osc.start(); lfo.start();
  srcAudio = dst.stream.getAudioTracks()[0];
}
function stopSources() {
  clearInterval(drawTimer);
  if (srcVideo) srcVideo.stop();
  if (srcAudio) srcAudio.stop();
  if (audioCtx) audioCtx.close();
}

// Chrome bildet Host- und ICE-TCP-Kandidaten nur, wenn die Seite eine
// Mikrofon- oder Kamera-Freigabe hat (sonst nur die Standardroute ueber UDP).
// In echten Anrufen ist die Freigabe da. Die Probe fragt deshalb einmal nach
// dem Mikrofon und schliesst es sofort wieder; aufgenommen wird nichts.
async function unlockCandidates() {
  if (Q.get('mic') === '0' || !navigator.mediaDevices) return 'aus';
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
    return 'erteilt';
  } catch (e) {
    return 'abgelehnt';
  }
}

// ---------- Dummy-E2E ----------
function e2e(x, op) {
  if (!worker) return;
  try {
    if (useScript) x.transform = new RTCRtpScriptTransform(worker, { op });
    else if (x.createEncodedStreams) {
      const { readable, writable } = x.createEncodedStreams();
      worker.postMessage({ op, readable, writable }, [readable, writable]);
    }
  } catch (e) { console.warn('e2e', e); }
}

// ---------- WSS-Ping ----------
const wsSamples = []; // [zeit, rtt_ms]
let ws = null, wsTimer = null;
function startPing() {
  ws = new WebSocket(`wss://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const t0 = +ev.data;
    if (t0) wsSamples.push([performance.now(), performance.now() - t0]);
  };
  ws.onopen = () => {
    wsTimer = setInterval(() => { if (ws.readyState === 1) ws.send(String(performance.now())); }, 500);
  };
}
function stopPing() { clearInterval(wsTimer); if (ws) ws.close(); }
function wsWindow(t0, t1) {
  const v = wsSamples.filter(([t]) => t >= t0 && t <= t1).map(([, r]) => r);
  return { p50: r1(pct(v, 0.5)), p95: r1(pct(v, 0.95)), n: v.length };
}

// ---------- Beine ----------
async function api(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}

// Jitter-Puffer-Ziel auf TCP-Wegen (wie der SFU-Client): 150 ms an allen
// Bild-Empfaengern. Ton bleibt beim Standard (NetEQ), wie im SFU-Client.
const JBT = 150;
const jbtSupported = typeof RTCRtpReceiver !== 'undefined' && 'jitterBufferTarget' in RTCRtpReceiver.prototype;
function setJbt(receiver) {
  if (!jbtSupported) return false;
  try { receiver.jitterBufferTarget = JBT; return true; } catch (e) { return false; }
}

function encodings(path) {
  // TCP-Wege: 2 Schichten (Kacheln 180p/10 fps wie der SFU auf TCP, Sprecher
  // 540p), UDP: 3 Schichten (H4), Kacheln dort 15 fps
  const l = { rid: 'l', scaleResolutionDownBy: 3, maxBitrate: 250000, maxFramerate: path === 'udp' ? 15 : 10 };
  const m = { rid: 'm', scaleResolutionDownBy: 1.5, maxBitrate: 600000, maxFramerate: 30 };
  const h = { rid: 'h', scaleResolutionDownBy: 1, maxBitrate: 1500000, maxFramerate: 30 };
  return path === 'udp' ? [l, m, h] : [l, h];
}

async function openLeg(path, sid, spec) {
  const cfg = { bundlePolicy: 'max-bundle', rtcpMuxPolicy: 'require' };
  if (path === 'turn') {
    const port = location.port || '443';
    cfg.iceServers = [{ urls: `turns:${location.hostname}:${port}?transport=tcp`, username: session.turn.username, credential: session.turn.credential }];
    cfg.iceTransportPolicy = 'relay';
  }
  if (!useScript) cfg.encodedInsertableStreams = true;
  const pc = new RTCPeerConnection(cfg);
  const leg = { pc, spec, id: null, t0: performance.now(), connectMs: null, videos: [], jbtSet: 0, jbtFail: 0 };
  if (spec.sendAudio) e2e(pc.addTransceiver(srcAudio, { direction: 'sendonly' }).sender, 'send');
  if (spec.sendVideo) e2e(pc.addTransceiver(srcVideo, { direction: 'sendonly', sendEncodings: encodings(path) }).sender, 'send');
  for (let i = 0; i < spec.audio; i++) e2e(pc.addTransceiver('audio', { direction: 'recvonly' }).receiver, 'recv');
  for (let i = 0; i < spec.video; i++) e2e(pc.addTransceiver('video', { direction: 'recvonly' }).receiver, 'recv');
  pc.ontrack = (ev) => {
    if (path !== 'udp' && ev.track.kind === 'video') {
      if (setJbt(ev.receiver)) leg.jbtSet++; else leg.jbtFail++;
    }
    const ms = new MediaStream([ev.track]);
    if (ev.track.kind === 'audio') {
      const a = new Audio();
      a.srcObject = ms; a.volume = 0; a.play().catch(() => {});
      leg.videos.push(a);
    } else {
      const v = document.createElement('video');
      v.muted = true; v.autoplay = true; v.playsInline = true; v.srcObject = ms;
      if (!$('tiles').querySelector('video')) v.className = 'speaker';
      $('tiles').appendChild(v);
      leg.videos.push(v);
    }
  };
  const connected = new Promise((resolve) => {
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'connected') { leg.connectMs = Math.round(performance.now() - leg.t0); resolve(true); }
      if (pc.connectionState === 'failed') resolve(false);
    };
  });
  // Kein Warten auf Kandidaten: der Knoten ist ICE-lite und lernt die
  // Gegenadresse aus den Pruefungen des Geraets (peer-reflexiv).
  await pc.setLocalDescription(await pc.createOffer());
  const res = await api('POST', '/api/leg', { sid, path, role: spec.role, audio: spec.audio, video: spec.video, offer: pc.localDescription.sdp });
  leg.id = res.id;
  await pc.setRemoteDescription({ type: 'answer', sdp: res.answer });
  leg.ok = await Promise.race([connected, sleep(TIMEOUT[path]).then(() => false)]);
  return leg;
}

async function closeLeg(leg) {
  let node = null;
  if (leg.id) node = await api('DELETE', '/api/leg/' + leg.id).catch(() => null);
  leg.pc.close();
  for (const v of leg.videos) { v.srcObject = null; v.remove(); }
  return node;
}

// ---------- getStats ----------
async function snapshot(pc) {
  const rep = await pc.getStats();
  const s = { inBytes: 0, outBytes: 0, rtt: null, cand: null, a: { conc: 0, tot: 0, jbd: 0, jbe: 0, lost: 0, recv: 0, jit: [] , n: 0 },
    v: { frz: 0, frzDur: 0, dec: 0, lost: 0, recv: 0, n: 0, haveFreeze: false }, ql: null, outRes: [] };
  let pair = null;
  rep.forEach((x) => {
    if (x.type === 'transport' && x.selectedCandidatePairId) pair = rep.get(x.selectedCandidatePairId);
  });
  if (!pair) rep.forEach((x) => { if (x.type === 'candidate-pair' && (x.selected || (x.nominated && x.state === 'succeeded'))) pair = x; });
  if (pair) {
    if (pair.currentRoundTripTime != null) s.rtt = pair.currentRoundTripTime * 1000;
    const lc = rep.get(pair.localCandidateId);
    if (lc) s.cand = { protocol: lc.protocol, type: lc.candidateType, relayProtocol: lc.relayProtocol || null };
  }
  rep.forEach((x) => {
    if (x.type === 'inbound-rtp') {
      s.inBytes += x.bytesReceived || 0;
      if (x.kind === 'audio') {
        const a = s.a; a.n++;
        a.conc += (x.concealedSamples || 0) - (x.silentConcealedSamples || 0);
        a.tot += x.totalSamplesReceived || 0;
        a.jbd += x.jitterBufferDelay || 0; a.jbe += x.jitterBufferEmittedCount || 0;
        a.lost += Math.max(0, x.packetsLost || 0); a.recv += x.packetsReceived || 0;
        if (x.jitter != null) a.jit.push(x.jitter * 1000);
      } else if (x.kind === 'video') {
        const v = s.v; v.n++;
        if (x.freezeCount != null) v.haveFreeze = true;
        v.frz += x.freezeCount || 0; v.frzDur += x.totalFreezesDuration || 0;
        v.dec += x.framesDecoded || 0;
        v.lost += Math.max(0, x.packetsLost || 0); v.recv += x.packetsReceived || 0;
      }
    } else if (x.type === 'outbound-rtp') {
      s.outBytes += x.bytesSent || 0;
      if (x.kind === 'audio') s.outAudioPkts = (s.outAudioPkts || 0) + (x.packetsSent || 0);
      if (x.kind === 'video') s.outVideoPkts = (s.outVideoPkts || 0) + (x.packetsSent || 0);
      if (x.qualityLimitationReason && x.qualityLimitationReason !== 'none') s.ql = x.qualityLimitationReason;
      if (x.kind === 'video' && x.frameHeight) s.outRes.push((x.rid || '-') + ':' + x.frameHeight);
    }
  });
  return s;
}

async function measure(legs, secs, onTick) {
  const snaps = []; // [zeit, [snap je bein]]
  const t0 = performance.now();
  for (let i = 0; i <= secs + WARM; i++) {
    const now = performance.now();
    snaps.push([now, await Promise.all(legs.map((l) => snapshot(l.pc)))]);
    onTick && onTick(i / (secs + WARM));
    const wait = t0 + (i + 1) * 1000 - performance.now();
    if (i < secs + WARM) await sleep(Math.max(0, wait));
  }
  return snaps;
}

function evaluate(snaps, legs, tStart, tEnd) {
  const win = snaps.slice(WARM); // Anlauf ohne Wertung
  if (win.length < 2) return {};
  const first = win[0][1], last = win[win.length - 1][1];
  const secs = (win[win.length - 1][0] - win[0][0]) / 1000;
  const sum = (arr, f) => arr.reduce((acc, x) => acc + f(x), 0);
  const rtts = [];
  const down = [], up = [];
  for (let i = 1; i < win.length; i++) {
    const dt = (win[i][0] - win[i - 1][0]) / 1000;
    down.push((sum(win[i][1], (s) => s.inBytes) - sum(win[i - 1][1], (s) => s.inBytes)) * 8 / 1000 / dt);
    up.push((sum(win[i][1], (s) => s.outBytes) - sum(win[i - 1][1], (s) => s.outBytes)) * 8 / 1000 / dt);
    for (const s of win[i][1]) if (s.rtt != null) rtts.push(s.rtt);
  }
  const d = (f) => sum(last, f) - sum(first, f);
  const aTot = d((s) => s.a.tot), aConc = d((s) => s.a.conc);
  const aJbe = d((s) => s.a.jbe), aJbd = d((s) => s.a.jbd);
  const aLost = d((s) => s.a.lost), aRecv = d((s) => s.a.recv);
  const vLost = d((s) => s.v.lost), vRecv = d((s) => s.v.recv);
  const vTracks = sum(last, (s) => s.v.n);
  const haveFreeze = last.some((s) => s.v.haveFreeze);
  const frz = d((s) => s.v.frz), frzDur = d((s) => s.v.frzDur);
  const jit = last.flatMap((s) => s.a.jit);
  const stable = down.slice(Math.floor(down.length / 2));
  const stableUp = up.slice(Math.floor(up.length / 2));
  const cand = last.map((s) => s.cand).filter(Boolean);
  return {
    secs: r1(secs),
    cand: cand[0] || null,
    cand_all: cand,
    rtt_ms: { p50: r1(pct(rtts, 0.5)), p95: r1(pct(rtts, 0.95)), n: rtts.length },
    ws_rtt_ms: wsWindow(tStart, tEnd),
    audio: {
      tracks: sum(last, (s) => s.a.n),
      conceal_pct: aTot > 0 ? r2(100 * aConc / aTot) : null,
      jb_ms: aJbe > 0 ? r1(1000 * aJbd / aJbe) : null,
      jitter_ms: r1(pct(jit, 0.5)),
      loss_pct: aRecv + aLost > 0 ? r2(100 * aLost / (aRecv + aLost)) : null,
    },
    video: vTracks ? {
      tracks: vTracks,
      freezes: haveFreeze ? frz : null,
      freeze_s: haveFreeze ? r2(frzDur) : null,
      freezes_per5min_track: haveFreeze ? r2(frz / vTracks / (secs / 300)) : null,
      freeze_s_per5min_track: haveFreeze ? r2(frzDur / vTracks / (secs / 300)) : null,
      fps: r1(d((s) => s.v.dec) / vTracks / secs),
      loss_pct: vRecv + vLost > 0 ? r2(100 * vLost / (vRecv + vLost)) : null,
    } : null,
    down_kbps: { stable: Math.round(pct(stable, 0.5) || 0), mean: Math.round(stable.reduce((a, b) => a + b, 0) / (stable.length || 1)) },
    up_kbps: { stable: Math.round(pct(stableUp, 0.5) || 0) },
    quality_limit: last.map((s) => s.ql).find(Boolean) || null,
    send_layers: last.flatMap((s) => s.outRes),
    // Diagnose je Bein: gesendete Pakete (Ton/Bild) und empfangene Spuren
    legs_diag: last.map((s) => ({ out_a: s.outAudioPkts || 0, out_v: s.outVideoPkts || 0, in_a: s.a.n, in_v: s.v.n })),
    src_audio: srcAudio ? { muted: srcAudio.muted, state: srcAudio.readyState, ctx: audioCtx && audioCtx.state } : null,
  };
}

async function runMedia(path, profKey, secs, prog) {
  const prof = PROFILES[profKey];
  const sid = rid();
  const specs = path === 'udp' || prof.video === 0
    ? [{ role: prof.video ? 'both' : 'audio', audio: prof.audio, video: prof.video, sendAudio: true, sendVideo: prof.video > 0 }]
    : [{ role: 'audio', audio: prof.audio, video: 0, sendAudio: true, sendVideo: false }, // H1: Ton und Bild getrennt
       { role: 'video', audio: 0, video: prof.video, sendAudio: false, sendVideo: true }];
  const res = { path, profile: profKey, n: prof.n, ok: false, legs: specs.map((s) => s.role) };
  const legs = [];
  try {
    for (const sp of specs) legs.push(await openLeg(path, sid, sp));
    res.connect_ms = legs.map((l) => l.connectMs);
    if (path !== 'udp') {
      // Jitter-Puffer-Ziel und Kachelprofil im Ergebnis (Safari: sehen, ob es greift)
      res.jbt = jbtSupported && legs.every((l) => !l.jbtFail) ? JBT : 'nicht unterstützt';
      if (prof.video) res.tile_profile = '180p/10fps';
    } else if (prof.video) res.tile_profile = '180p/15fps';
    if (!legs.every((l) => l.ok)) {
      res.reason = 'nicht erreichbar';
      return res;
    }
    res.ok = true;
    const tStart = performance.now() + WARM * 1000;
    const snaps = await measure(legs, secs, prog);
    Object.assign(res, evaluate(snaps, legs, tStart, performance.now()));
  } catch (e) {
    res.reason = String(e.message || e);
  } finally {
    res.node = [];
    for (const l of legs) {
      const n = await closeLeg(l);
      if (n) res.node.push(n);
    }
  }
  return res;
}

// ---------- Ablauf ----------
function phaseList(paths) {
  const ph = [{ key: 'base', label: 'Grundmessung (WSS-Ping)', secs: BASE }];
  for (const p of paths) ph.push({ key: p, label: `${PATHS[p]} · 12 Video`, secs: DUR, path: p, prof: 'v12' });
  ph.push({ key: 'ton', label: 'TCP-Weg · 25 Ton', secs: TON, prof: 'a25' });
  return ph;
}

// Text oder Liste von Teilen; Teile brechen nur zwischen den Teilen um
function setStatus(li, text, cls) {
  const st = li.querySelector('.st');
  st.textContent = ''; st.className = 'st ' + (cls || '');
  (Array.isArray(text) ? text : [text]).forEach((t, i) => {
    if (i) st.append(' · ');
    const s = document.createElement('span'); s.textContent = t; st.appendChild(s);
  });
}

async function run(net, dev) {
  $('go').disabled = true;
  $('lauf').hidden = false; $('erg').hidden = true;
  let paths = (Q.get('paths') || 'udp,tcp,turn').split(',').filter((p) => PATHS[p]);
  if (!session.udp) paths = paths.filter((p) => p !== 'udp');
  const ph = phaseList(paths);
  const total = ph.reduce((a, p) => a + p.secs + (p.prof ? WARM + 3 : 0), 0);
  $('phasen').innerHTML = '';
  const lis = ph.map((p) => {
    const li = document.createElement('li');
    const a = document.createElement('span'); a.textContent = p.label;
    const b = document.createElement('span'); b.className = 'st'; b.textContent = 'wartet';
    li.append(a, b); $('phasen').appendChild(li);
    return li;
  });
  if (!session.udp && (Q.get('paths') || 'udp').includes('udp')) {
    const li = document.createElement('li');
    li.innerHTML = '<span>UDP · 12 Video</span><span class="st">am Knoten aus</span>';
    $('phasen').prepend(li);
  }
  let done = 0;
  const progress = (secs) => (f) => { $('fill').style.width = Math.min(100, 100 * (done + f * secs) / total) + '%'; };
  worker = new Worker('/e2e-worker.js');
  const mic = await unlockCandidates();
  startSources();
  startPing();
  const result = { v: 1, net, device: dev, ...browserInfo(), mic_permission: mic, e2e: useScript ? 'RTCRtpScriptTransform' : 'createEncodedStreams',
    jbt: jbtSupported ? JBT : 'nicht unterstützt',
    profile_secs: { base: BASE, path: DUR, ton: TON }, phases: [] };
  if (!session.udp) result.phases.push({ path: 'udp', profile: 'v12', ok: false, reason: 'am Knoten aus' });
  try {
    for (let i = 0; i < ph.length; i++) {
      const p = ph[i], li = lis[i];
      setStatus(li, 'läuft …', 'run');
      if (p.key === 'base') {
        const t0 = performance.now();
        for (let s = 0; s < p.secs; s++) { progress(p.secs)(s / p.secs); await sleep(1000); }
        const w = wsWindow(t0, performance.now());
        result.base = { ws_rtt_ms: w };
        setStatus(li, w.n ? `RTT ${w.p50} / ${w.p95} ms` : 'kein Ping', w.n ? 'ok' : 'bad');
      } else {
        let path = p.path;
        if (p.key === 'ton') {
          const ok = (k) => result.phases.find((x) => x.path === k && x.ok);
          path = ok('tcp') ? 'tcp' : ok('turn') ? 'turn' : 'turn';
          li.firstChild.textContent = `${PATHS[path]} · 25 Ton`;
        }
        const r = await runMedia(path, p.prof, p.secs, progress(p.secs + WARM));
        result.phases.push(r);
        if (r.ok) {
          const txt = [`Aufbau ${Math.max(...r.connect_ms)} ms`, r.rtt_ms && r.rtt_ms.p95 != null ? `RTT p95 ${r.rtt_ms.p95} ms` : null,
            r.audio && r.audio.conceal_pct != null ? `Verdeckung ${r.audio.conceal_pct} %` : null].filter(Boolean);
          setStatus(li, txt, 'ok');
        } else setStatus(li, r.reason || 'fehlgeschlagen', 'bad');
      }
      done += p.secs + (p.prof ? WARM + 3 : 0);
    }
  } finally {
    stopPing(); stopSources(); worker.terminate(); worker = null;
    $('fill').style.width = '100%';
  }
  try {
    const s = await api('POST', '/api/results', result);
    $('saved').textContent = `Gespeichert als ${s.file}. Bericht: gjallarhorn report`;
    result.file = s.file;
  } catch (e) {
    $('saved').textContent = 'Speichern fehlgeschlagen: ' + e.message;
  }
  showTable(result);
  window.__hornResult = result;
  $('go').disabled = false;
}

function showTable(r) {
  const t = $('tab');
  t.innerHTML = '';
  const head = ['Weg', 'Profil', 'Aufbau ms', 'RTT p50/p95', 'Verdeckung %', 'Puffer ms', 'Einfrieren /5min', 'Runter Mbit/s', 'Rauf Mbit/s', 'Verworfen'];
  const tr = document.createElement('tr'); tr.className = 'kopf';
  for (const h of head) { const th = document.createElement('th'); th.textContent = h; tr.appendChild(th); }
  t.appendChild(tr);
  for (const p of r.phases) {
    const row = document.createElement('tr');
    const dropped = (p.node || []).reduce((a, n) => a + (n.frames_dropped || 0), 0);
    const cells = p.ok ? [PATHS[p.path], PROFILES[p.profile].label, Math.max(...p.connect_ms),
      `${p.rtt_ms.p50 ?? '–'} / ${p.rtt_ms.p95 ?? '–'}`, p.audio.conceal_pct ?? '–', p.audio.jb_ms ?? '–',
      p.video ? (p.video.freezes_per5min_track ?? '–') : '–', r2(p.down_kbps.stable / 1000), r2(p.up_kbps.stable / 1000), dropped]
      : [PATHS[p.path], PROFILES[p.profile].label, p.reason || '✘', '', '', '', '', '', '', ''];
    // data-label traegt die Spaltenueberschrift fuer die Kartenansicht (schmale Bildschirme)
    cells.forEach((c, i) => {
      const td = document.createElement('td'); td.textContent = c ?? '–'; td.dataset.label = head[i];
      const cl = [i === 0 ? 'kw' : i === 1 ? 'kp' : ''];
      if (i === 2 && !p.ok) cl.push('bad', 'grund');
      if (!p.ok && i > 2) cl.push('leer');
      td.className = cl.filter(Boolean).join(' ');
      row.appendChild(td);
    });
    if (!p.ok) row.className = 'fehl';
    t.appendChild(row);
  }
  $('jbt').textContent = `Jitter-Puffer-Ziel auf TCP-Wegen (Bild): ${typeof r.jbt === 'number' ? r.jbt + ' ms' : r.jbt}` +
    ` · Kacheln auf TCP 180p/10 fps, auf UDP 180p/15 fps`;
  $('erg').hidden = false;
}

async function init() {
  session = await api('GET', '/api/session');
  $('ver').textContent = 'Knoten ' + session.version + (session.udp ? ' · UDP an' : ' · nur TCP');
  const paths = (Q.get('paths') || 'udp,tcp,turn').split(',').filter((p) => PATHS[p] && (p !== 'udp' || session.udp));
  $('dauer').textContent = Math.round((BASE + paths.length * (DUR + WARM) + TON + WARM) / 60 * 10) / 10;
  $('f').onsubmit = (ev) => { ev.preventDefault(); run($('net').value.trim(), $('dev').value); };
  if (Q.get('net')) $('net').value = Q.get('net');
  window.__hornReady = true;
}
init().catch((e) => { $('ver').textContent = 'Fehler: ' + e.message; });
