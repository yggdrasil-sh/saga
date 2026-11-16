// B2-Messseite (Produktfassung, gjallarhorn/e2e/b2mess): ein Geraet im Testraum des Knotens (HORN_DEV=1), wahlweise
//   path=ws   : Rahmen-Weg mit den Produkt-Dateien static/horn-frames.js + static/frame-e2e.js (Schleife, Attrappen, zwei
//               Rahmen-WebSockets an /api/sfu/ws durch den NPM-Nachbau, E2E Variante W)
//   path=turn : heutiger Weg TURN-TLS am Knoten (Vergleich, E2E wie static/calls.js), path=tcp: ICE-TCP
// Immer mit Frame-E2E (Testschluessel je Geraet). Nach dem Spike ws-browser (ws.js), Messgroessen unveraendert.
// Aufruf: b2.html?room=r&peer=p1&path=ws&hue=120&tiles=3&turn=localhost:8597
// Messwerte: window.__rep() (Empfang je Platz, Latenz Bild per Zeitcode, Ton per Pieps, WebSocket-Zustand).
'use strict';
const Q = new URLSearchParams(location.search);
const room = Q.get('room') || 'test';
const peer = Q.get('peer') || 'p' + Math.floor(Math.random() * 1e6);
const path = Q.get('path') || 'ws';
const hue = +(Q.get('hue') || 0);
const loud = Q.get('loud') === '1';
const tiles = +(Q.get('tiles') || 3);
const jbTarget = +(Q.get('jb') || 150);
const turnAddr = Q.get('turn') || location.host;
const wsBase = Q.get('wsbase') || `wss://${location.host}`;
const vol = +(Q.get('vol') || 0);
const sendVideo = Q.get('video') !== '0';
// b2-vorab: E2E wie heute (static/frame-e2e.js). e2e=w: ws-Weg oeffnet im Worker vor dem Einspeisen, e2e=r: am
// pcB-Empfaenger (frame-e2e.js unveraendert, eigener Worker); turn: jeder Wert != '' = Sender/Empfaenger wie im Anruf.
// e2enokey=1: keine Schluessel der anderen (Gegenprobe), e2eraw=1: Empfang ohne Oeffnen (Chiffrat an den Decoder).
const e2e = 'w';
const isFF = /Firefox\//.test(navigator.userAgent);
const FWD = Q.get('fwd') === '1';   // Sende-Schleife mit Rueckmeldung (Firefox-Zielrate), siehe ws-worker.js
const e2eNoKey = Q.get('e2enokey') === '1', e2eRaw = Q.get('e2eraw') === '1';
const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const wall = () => performance.timeOrigin + performance.now();
const S = { peer, path, hue, ready: false, error: null, recv: [], lat: [], beeps: [], ev: [], t0: performance.now(), first: {} };
window.__sfu = S;

async function api(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(url + ': ' + (j.error || r.status));
  return j;
}

// ---------- Quellen: Farbflaeche + Rauschfeld + Zeitcode; Ton 200+hue Hz mit Pieps jede volle Sekunde ----------
let srcVideo, srcAudio, ac;
function startSources() {
  const cv = $('src'), g = cv.getContext('2d');
  let f = 0;
  const draw = () => {
    f++;
    g.fillStyle = `hsl(${hue} 80% 50%)`; g.fillRect(0, 0, 640, 360);
    const x = (f * 7) % 520;
    for (let i = 0; i < 40; i++) { const v = (Math.random() * 255) | 0; g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(x + (i % 8) * 15, 100 + ((i / 8) | 0) * 15, 15, 15); }
    TC.draw(g, 640, 360, wall());
  };
  setInterval(draw, 1000 / 30);
  srcVideo = cv.captureStream(30).getVideoTracks()[0];
  ac = new AudioContext({ sampleRate: 48000 });
  const osc = ac.createOscillator(); osc.frequency.value = 200 + hue;
  const gain = ac.createGain(); const base = loud ? 0.08 : 0.04; gain.gain.value = base;
  const dst = ac.createMediaStreamDestination();
  osc.connect(gain).connect(dst); osc.start();
  srcAudio = dst.stream.getAudioTracks()[0];
  if (Q.get('mic')) {   // AEC-Messung: echtes Mikrofon (PulseAudio-Schleife) statt Ton-Generator
    navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: Q.get('mic') === 'aec', noiseSuppression: false, autoGainControl: false } })
      .then((ms) => { S.micReady = ms.getAudioTracks()[0]; });
  }
  // Pieps (0,5) fuer 60 ms an jeder vollen Wanduhr-Sekunde; Zuordnung Kontextzeit <-> Wanduhr ueber getOutputTimestamp
  const sched = () => {
    const ot = ac.getOutputTimestamp();
    const ctxNow = ot.contextTime != null && ot.performanceTime ? ot.contextTime + (performance.now() - ot.performanceTime) / 1000 : ac.currentTime;
    const w = wall(), next = Math.ceil(w / 1000) * 1000;
    for (let k = 0; k < 3; k++) {
      const at = ctxNow + (next + k * 1000 - w) / 1000;
      gain.gain.setValueAtTime(0.5, at); gain.gain.setValueAtTime(base, at + 0.06);
    }
  };
  sched(); setInterval(sched, 3000);
}

// Pieps-Erkennung je Ton-Platz (AudioWorklet): Einsatz ueber Schwelle nach >= 400 ms Ruhe -> Abstand zur vollen Sekunde
let beepReady = null;
async function beepDetector(track, slot) {
  if (!beepReady) {
    const code = `class B extends AudioWorkletProcessor{constructor(){super();this.q=0;this.on=false;this.lv=0;}
      process(i){const c=i[0]&&i[0][0];if(!c)return true;let e=0;for(let k=0;k<c.length;k++)e+=c[k]*c[k];e=Math.sqrt(e/c.length);
      if(!this.on&&e>0.2&&this.q>=150){this.on=true;this.port.postMessage(currentTime);} if(e<0.12){this.q++;if(this.q>=150)this.on=false;}else this.q=0; return true;}}
      registerProcessor('beep',B);`;
    beepReady = ac.audioWorklet.addModule(URL.createObjectURL(new Blob([code], { type: 'application/javascript' })));
  }
  await beepReady;
  const n = new AudioWorkletNode(ac, 'beep');
  n.port.onmessage = (e) => {
    const ot = ac.getOutputTimestamp();
    const ctxNow = ot.contextTime + (performance.now() - ot.performanceTime) / 1000;
    const w = wall() - (ctxNow - e.data) * 1000;     // Wanduhr des Blocks
    let lag = w % 1000; if (lag > 900) lag -= 1000;
    S.beeps.push([Math.round(performance.now()), slot, Math.round(lag)]);
  };
  const m = ac.createGain(); m.gain.value = 0;
  ac.createMediaStreamSource(new MediaStream([track])).connect(n); n.connect(m).connect(ac.destination);
}

// ---------- Empfangsanzeige + Zeitcode-Latenz ----------
function attach(kind, track, receiver, slot) {
  const r = { kind, slot, receiver, el: null, track };
  if (kind === 'audio') {
    const a = new Audio(); a.srcObject = new MediaStream([track]); a.volume = vol; a.play().catch(() => {}); r.el = a;
    r.an = ac.createAnalyser(); r.an.fftSize = 8192; ac.createMediaStreamSource(new MediaStream([track])).connect(r.an);
    beepDetector(track, slot);
  } else {
    const v = document.createElement('video');
    v.muted = true; v.autoplay = true; v.playsInline = true; v.srcObject = new MediaStream([track]);
    $('tiles').appendChild(v); r.el = v;
    const onvf = (nowT, md) => {
      if (!S.first[slot]) S.first[slot] = Math.round(performance.now() - S.t0);
      try {
        if (v.videoWidth >= 160) {
          const c = TC.read(v, v.videoWidth, v.videoHeight);
          S.lat.push([Math.round(performance.now()), slot, TC.lag(c, performance.timeOrigin + nowT), v.videoHeight]);
          if (S.lat.length > 20000) S.lat.splice(0, 5000);
        }
      } catch (e) { /* */ }
      v.requestVideoFrameCallback(onvf);
    };
    v.requestVideoFrameCallback(onvf);
  }
  S.recv.push(r);
  return r;
}

// ---------- E2E: Testschluessel je Geraet = SHA-256("b2-test/" + peer), Zuordnung CSRC -> Geraet aus dem Testraum ----------
let FE = null;
const E2 = { known: {} };
async function testKey(name) { return new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode('b2-test/' + name))); }
async function e2eInit(mapKey) {
  FE = FE || SagaFrameE2E.create({ url: '/static/frame-e2e.js' });
  await FE.own('start', (await testKey(peer)).buffer);
  const poll = async () => {
    const r = await fetch(`/api/dev/room/${room}`).then((x) => x.json()).catch(() => ({}));
    const map = {};
    for (const [p, c] of Object.entries(r.peers || {})) {
      const name = p.replace(/-(audio|video)$/, '');
      map[c >>> 0] = name;
      if (name !== peer && !E2.known[name] && !e2eNoKey) {
        E2.known[name] = true;
        await FE.peer(name, 0, (await testKey(name)).buffer, 'aaaaaaaaaaaaaaaa');
      }
    }
    await FE.csrcMap(map, mapKey);
  };
  await poll(); S.e2ePoll = setInterval(poll, 1000);
}

// ---------- heutiger Weg: TURN-TLS / ICE-TCP (wie /dev/sfu.js) ----------
const legs = {};
async function openLeg(role, turn) {
  const tok = (await api('POST', '/api/dev/token', { room, peer, role, device: 'desktop' })).token;
  const cfg = { bundlePolicy: 'max-bundle', rtcpMuxPolicy: 'require' };
  if (path === 'turn') {
    cfg.iceServers = [{ urls: `turns:${turnAddr.replace(/:.*/, '')}:${turnAddr.split(':')[1] || 443}?transport=tcp`, username: turn.username, credential: turn.credential }];
    cfg.iceTransportPolicy = 'relay';
  }
  const pc = new RTCPeerConnection(cfg);
  const stream = new MediaStream(role === 'audio' ? [srcAudio] : [srcVideo]);
  let n = 0;
  if (role === 'audio') {
    pc.addTransceiver(srcAudio, { direction: 'sendonly', streams: [stream] });
    for (let i = 0; i < 3; i++) pc.addTransceiver('audio', { direction: 'recvonly' });
  } else {
    if (sendVideo) pc.addTransceiver(srcVideo, { direction: 'sendonly', streams: [stream], sendEncodings: [
      { rid: 'l', scaleResolutionDownBy: 4, maxBitrate: 150000, maxFramerate: 20, scalabilityMode: 'L1T2' },
      { rid: 'm', scaleResolutionDownBy: 1, maxBitrate: 700000, maxFramerate: 30, scalabilityMode: 'L1T3' }] });
    for (let i = 0; i < 1 + tiles; i++) pc.addTransceiver('video', { direction: 'recvonly' });
  }
  // wie static/calls.js: Empfaenger direkt nach addTransceiver einhaengen (in ontrack kommt Chromium zu spaet)
  for (const t of pc.getTransceivers()) { if (t.sender.track) FE.sender(t.sender, t.sender.track.kind); else if (!e2eRaw) FE.receiver(t.receiver, t.receiver.track.kind, '*', ''); }
  pc.ontrack = (ev) => {
    try { if (jbTarget >= 0 && ev.track.kind === 'video') ev.receiver.jitterBufferTarget = jbTarget; } catch (e) { /* */ }
    attach(ev.track.kind, ev.track, ev.receiver, (ev.track.kind === 'audio' ? 'a' : 'v') + (n++));
  };
  await pc.setLocalDescription(await pc.createOffer());
  const res = await api('POST', '/api/sfu/leg', { token: tok, path, offer: pc.localDescription.sdp, audio: role === 'audio' ? 3 : 0, speaker: role === 'video', tiles: role === 'video' ? tiles : 0 });
  S.csrc = res.csrc;
  await pc.setRemoteDescription({ type: 'answer', sdp: res.answer });
  legs[role] = pc;
  pc.onconnectionstatechange = () => S.ev.push([Math.round(performance.now() - S.t0), role, pc.connectionState]);
}

// ---------- Rahmen-Weg (Produktfassung) ----------
let F = null;
async function openB2() {
  await e2eInit('c1');
  let rs = 0;
  F = SagaHornFrames.create(FE, { key: 'c1', url: '/static/horn-frames.js', tiles, audioTrack: srcAudio, camTrack: sendVideo ? srcVideo : null,
                                  tx: { l: 150, m: 600, screen: 900, screenFps: 8 }, jb: jbTarget, rseq: () => ++rs });
  F.cfg(600, 150);
  F.onRate = (k) => { F.applyRate(); S.ev.push([Math.round(performance.now() - S.t0), 'rate', k]); };
  const kf = F.kick; F.onKf = (tr) => { S.ev.push([Math.round(performance.now() - S.t0), 'kick', tr]); kf(tr); };
  // Plan wie calls.js hornTuneCam (zu zweit: l und m aktiv auf TCP-artigen Wegen)
  await F.cam.sender.setParameters({ encodings: [{ rid: 'l', active: true, maxBitrate: 150000 }, { rid: 'm', active: true, maxBitrate: 600000 }] });
  await F.ready;
  F.rx.audio.forEach((t, i) => attach('audio', t.receiver.track, t.receiver, 'a' + i));
  F.rx.video.forEach((t, i) => attach('video', t.receiver.track, t.receiver, 'v' + i));
  for (const role of ['audio', 'video']) await b2Join(role);
}
async function b2Join(role) {
  const tok = (await api('POST', '/api/dev/token', { room, peer, role, device: 'desktop' })).token;
  const join = role === 'audio' ? { t: 'join', v: 1, token: tok, audio: 3 } : { t: 'join', v: 1, token: tok, speaker: true, screen: true, tiles };
  const t0 = performance.now(), h = F.open(role, `${wsBase}/api/sfu/ws`, join);
  const ok = await h.ok;
  S.ev.push([Math.round(performance.now() - S.t0), 'ws-ok', role, Math.round(performance.now() - t0)]);
  if (role === 'video') S.csrc = ok.csrc;
  // Wiederverbinden wie calls.js (sofort, dann 0,5/1 s; frisches Token, gleiche leg_ref)
  h.on((code, cc) => {
    S.ev.push([Math.round(performance.now() - S.t0), 'closed', role, code || cc]);
    if (S.stop) return;
    rejoin(role);
  });
}
// Wiederholen bis es klappt: sofort, dann 0,5/1/2/4/8 s (die Seite hat keinen Neuaufbau ueber Saga wie calls.js)
function rejoin(role) {
  const n = S.rc[role] = (S.rc[role] || 0) + 1;
  setTimeout(() => b2Join(role).then(() => { S.rc[role] = 0; S.ev.push([Math.round(performance.now() - S.t0), 'rejoin-ok', role, n]); },
    (e) => { S.ev.push([Math.round(performance.now() - S.t0), 'rejoin-err', role, String(e)]); if (!S.stop) rejoin(role); }), n === 1 ? 0 : Math.min(8000, 250 * 2 ** (n - 1)));
}
S.rc = {};

// ---------- Messwerte ----------
const probe = document.createElement('canvas'); probe.width = 16; probe.height = 9;
function hueOf(v) {
  if (!v.videoWidth) return null;
  const g = probe.getContext('2d', { willReadFrequently: true });
  g.drawImage(v, 0, 0, 16, 9);
  const [r, gg, b] = g.getImageData(13, 7, 1, 1).data;
  const mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
  if (mx - mn < 30) return null;
  let h;
  if (mx === r) h = ((gg - b) / (mx - mn)) % 6; else if (mx === gg) h = (b - r) / (mx - mn) + 2; else h = (r - gg) / (mx - mn) + 4;
  return Math.round((h * 60 + 360) % 360);
}
window.__rep = async function () {
  const out = { peer, path, t: Math.round(performance.now() - S.t0), recv: [], ev: S.ev, first: S.first, csrc: S.csrc };
  for (const r of S.recv) {
    const x = { kind: r.kind, slot: r.slot };
    const st = await r.receiver.getStats();
    st.forEach((s) => {
      if (s.type !== 'inbound-rtp') return;
      x.bytes = s.bytesReceived || 0;
      if (r.kind === 'video') Object.assign(x, { dec: s.framesDecoded || 0, h: s.frameHeight || 0, fps: s.framesPerSecond || 0, frz: s.freezeCount || 0, frzS: s.totalFreezesDuration || 0, drop: s.framesDropped || 0, keys: s.keyFramesDecoded || 0, pli: s.pliCount || 0, jbd: s.jitterBufferDelay || 0, jbe: s.jitterBufferEmittedCount || 0 });
      else Object.assign(x, { samples: s.totalSamplesReceived || 0, conc: s.concealedSamples || 0, silConc: s.silentConcealedSamples || 0, ev: s.concealmentEvents || 0, jbd: s.jitterBufferDelay || 0, jbe: s.jitterBufferEmittedCount || 0, level: s.audioLevel || 0 });
    });
    if (r.kind === 'video') x.hue = hueOf(r.el);
    else if (r.an) {
      const f = new Float32Array(r.an.frequencyBinCount); r.an.getFloatFrequencyData(f);
      let bi = 1; for (let i = 1; i < f.length; i++) if (f[i] > f[bi]) bi = i;
      x.peakHz = Math.round(bi * ac.sampleRate / r.an.fftSize); x.peakDb = Math.round(f[bi]);
    }
    out.recv.push(x);
  }
  if (F) out.ws = { ws: { audio: Object.assign({}, F.st.audio, { rtts: F.st.audio && F.st.audio.rtt != null ? [F.st.audio.rtt] : [] }),
                         video: Object.assign({}, F.st.video, { rtts: F.st.video && F.st.video.rtt != null ? [F.st.video.rtt] : [] }) },
                    up: { target: F.st.video && F.st.video.up, rate: F.rate } };
  if (FE) out.fe = await FE.stats();
  // eigene Sendeseite
  const pcs = path === 'ws' ? [F && F.pcX] : Object.values(legs);
  out.send = {};
  for (const pc of pcs) {
    if (!pc) continue;
    (await pc.getStats()).forEach((s) => {
      if (s.type === 'outbound-rtp' && s.kind === 'video') out.send[s.rid || s.mid] = { w: s.frameWidth, fps: s.framesPerSecond, enc: s.framesEncoded, bytes: s.bytesSent, tgt: s.targetBitrate, qlr: s.qualityLimitationReason };
      if (s.type === 'media-source' && s.kind === 'video') out.srcFps = s.framesPerSecond;
      if (s.type === 'candidate-pair' && s.nominated && s.state === 'succeeded') out.rtt = s.currentRoundTripTime;
    });
  }
  out.lat = S.lat.splice(0); out.beeps = S.beeps.splice(0);
  return out;
};

async function main() {
  startSources();
  if (Q.get('mic')) { for (let i = 0; i < 50 && !S.micReady; i++) await sleep(100); if (S.micReady) { srcAudio = S.micReady; S.mic = srcAudio.getSettings(); } }
  try { (await navigator.mediaDevices.getUserMedia({ audio: true })).getTracks().forEach((t) => t.stop()); } catch (e) { /* ohne */ }
  await api('POST', '/api/dev/room', { room }).catch(() => {});
  S.tStart = Math.round(performance.now() - S.t0);
  if (path === 'ws') await openB2();
  else {
    let turn = null;
    if (path === 'turn') {
      const tok = (await api('POST', '/api/dev/token', { room, peer, role: 'audio' })).token;
      turn = await api('POST', '/api/sfu/turn', { token: tok });
    }
    await e2eInit('');
    await openLeg('audio', turn);
    await openLeg('video', turn);
  }
  S.ready = true;
  $('state').textContent = `${peer} · ${path} · bereit`;
}
main().catch((e) => { S.error = String(e && e.stack || e); $('state').textContent = 'Fehler: ' + e; });
