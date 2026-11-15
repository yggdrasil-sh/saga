// Testseite fuer den Weiterleiter (nur HORN_DEV=1). Ein Geraet mit Ton- und
// Bild-Leitung (TCP-Wege, H1). Aufruf: /dev/?room=r&peer=p1&path=tcp|turn&hue=120&loud=1
'use strict';
const Q = new URLSearchParams(location.search);
const room = Q.get('room') || 'test';
const peer = Q.get('peer') || 'p' + Math.floor(Math.random() * 1e6);
const path = Q.get('path') || 'tcp';
const hue = +(Q.get('hue') || 0);
const loud = Q.get('loud') === '1';
const tiles = +(Q.get('tiles') || 3);
const jbTarget = +(Q.get('jb') || 150);
// Pruefstand Engpass (e2e/stau.mjs): turn mit turnhost=h:p, tcp mit candport=p (Weg ueber e2e/engpass), Messmarken mark=1
const turnHost = Q.get('turnhost') || '';
const candPort = Q.get('candport') || '';
const mark = Q.get('mark') === '1';
const T0 = performance.timeOrigin;
const wall = () => T0 + performance.now();
const lat = { video: {}, audio: {} }; // je Quelle (Farbton) Liste der Ende-zu-Ende-Latenzen in ms
const $ = (id) => document.getElementById(id);
const state = { peer, path, hue, legs: {}, ready: false, error: null, recv: [] };
window.__sfu = state;

async function api(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(url + ': ' + (j.error || r.status));
  return j;
}

// Quelle: Flaeche im eigenen Farbton, bewegtes Rauschfeld (Encoder arbeitet)
let srcVideo, srcAudio, audioCtx, gain;
function startSources() {
  const cv = $('src');
  if (Q.get('w')) { cv.width = +Q.get('w'); cv.height = Math.round(cv.width * 9 / 16); } // kleinere Quelle (Lastlauf)
  const g = cv.getContext('2d');
  let f = 0;
  setInterval(() => {
    f++;
    g.fillStyle = `hsl(${hue} 80% 50%)`;
    g.fillRect(0, 0, cv.width, cv.height);
    const x = (f * 7) % (cv.width - 120);
    for (let i = 0; i < 40; i++) {
      const v = (Math.random() * 255) | 0;
      g.fillStyle = `rgb(${v},${v},${v})`;
      g.fillRect(x + (i % 8) * 15, 60 + ((i / 8) | 0) * 15, 15, 15);
    }
    if (mark) { // Zeitmarke: 16 Bit (ms mod 65536) als Balken oben, 40 px je Bit
      const t = Math.round(wall()) & 0xffff;
      const bw = cv.width / 16, bh = cv.height / 9;
      for (let b = 0; b < 16; b++) { g.fillStyle = (t >> (15 - b)) & 1 ? '#fff' : '#000'; g.fillRect(b * bw, 0, bw, bh); }
    }
  }, 1000 / 30);
  srcVideo = cv.captureStream(30).getVideoTracks()[0];
  audioCtx = new AudioContext();
  const osc = audioCtx.createOscillator();
  osc.frequency.value = 200 + hue;
  gain = audioCtx.createGain();
  gain.gain.value = loud ? 0.6 : 0.05;
  if (mark) { // Ton: Pieps 120 ms zu Beginn jeder vollen Sekunde (Uhr des Rechners, alle Seiten gleich)
    gain.gain.value = 0;
    const lvl = loud ? 0.6 : 0.3;
    const plan = () => {
      const now = wall(), ct = audioCtx.currentTime;
      for (let k = 1; k <= 4; k++) {
        const at = ct + ((Math.ceil(now / 1000) + k - 1) * 1000 - now) / 1000;
        if (at <= (state.beepPlanned || 0)) continue;
        gain.gain.setValueAtTime(lvl, at); gain.gain.setValueAtTime(0, at + 0.12);
        state.beepPlanned = at;
      }
    };
    plan(); setInterval(plan, 500);
  }
  const dst = audioCtx.createMediaStreamDestination();
  osc.connect(gain).connect(dst);
  osc.start();
  srcAudio = dst.stream.getAudioTracks()[0];
}

async function unlockCandidates() {
  try { (await navigator.mediaDevices.getUserMedia({ audio: true })).getTracks().forEach((t) => t.stop()); } catch (e) { /* ohne */ }
}

async function openLeg(role, turn) {
  const tok = (await api('POST', '/api/dev/token', { room, peer, role, device: 'desktop' })).token;
  const cfg = { bundlePolicy: 'max-bundle', rtcpMuxPolicy: 'require' };
  if (path === 'turn') {
    cfg.iceServers = [{ urls: `turns:${turnHost || (location.hostname + ':' + (location.port || 443))}?transport=tcp`, username: turn.username, credential: turn.credential }];
    cfg.iceTransportPolicy = 'relay';
  }
  const pc = new RTCPeerConnection(cfg);
  const leg = { role, pc, id: null, recv: [] };
  const stream = new MediaStream(role === 'audio' ? [srcAudio] : [srcVideo]);
  stream.id; // Stream-ID beginnt nicht mit "screen": Kamera
  if (role === 'audio') {
    pc.addTransceiver(srcAudio, { direction: 'sendonly', streams: [stream] });
    for (let i = 0; i < 3; i++) pc.addTransceiver('audio', { direction: 'recvonly' });
  } else {
    // TCP-Wege: 2 Schichten; Kachel-Schicht 20 fps L1T2 (Knoten nimmt 10 fps)
    pc.addTransceiver(srcVideo, { direction: 'sendonly', streams: [stream], sendEncodings: [
      { rid: 'l', scaleResolutionDownBy: 4, maxBitrate: 150000, maxFramerate: 20, scalabilityMode: 'L1T2' },
      { rid: 'm', scaleResolutionDownBy: 1, maxBitrate: 700000, maxFramerate: 30, scalabilityMode: 'L1T3' }] });
    for (let i = 0; i < 1 + tiles; i++) pc.addTransceiver('video', { direction: 'recvonly' });
  }
  pc.ontrack = (ev) => {
    const r = { kind: ev.track.kind, mid: ev.transceiver.mid, receiver: ev.receiver, el: null, t0: wall() };
    try { if (jbTarget >= 0 && 'jitterBufferTarget' in ev.receiver && ev.track.kind === 'video') ev.receiver.jitterBufferTarget = jbTarget; } catch (e) { /* alt */ }   // jb=-1: wie Apps ohne jitterBufferTarget
    const ms = new MediaStream([ev.track]);
    if (ev.track.kind === 'audio') {
      const a = new Audio(); a.srcObject = ms; a.volume = 0; a.play().catch(() => {}); r.el = a;
      // Analyse: Pegel und Spitzenfrequenz (Absender = 200 + Farbton Hz)
      try {
        r.an = audioCtx.createAnalyser();
        r.an.fftSize = 8192;
        const srcN = audioCtx.createMediaStreamSource(ms);
        srcN.connect(r.an);
        if (mark) beepDetector(srcN, r);
      } catch (e) { /* ohne */ }
    } else {
      const v = document.createElement('video');
      v.muted = true; v.autoplay = true; v.playsInline = true; v.srcObject = ms; v.width = 160; v.height = 90;
      $('tiles').appendChild(v); r.el = v;
    }
    leg.recv.push(r);
    state.recv.push(r);
  };
  await pc.setLocalDescription(await pc.createOffer());
  leg.t0 = wall();
  const res = await api('POST', '/api/sfu/leg', { token: tok, path, offer: pc.localDescription.sdp, audio: role === 'audio' ? 3 : 0, speaker: role === 'video', tiles: role === 'video' ? tiles : 0 });
  leg.id = res.id;
  state.csrc = res.csrc;
  let ans = res.answer;
  // candport: ICE-TCP-Kandidat des Knotens auf den Engpass biegen
  const bend = path === 'tcp' && candPort ? candPort : '';
  if (bend) ans = ans.replace(/^(a=candidate:\S+ \d+ tcp \d+ )(\S+) (\d+)( typ host tcptype passive.*)$/gim, (m, a, ip, port, b) => `${a}127.0.0.1 ${bend}${b}`);
  await pc.setRemoteDescription({ type: 'answer', sdp: ans });
  pc.addEventListener('connectionstatechange', () => { if (pc.connectionState === 'connected' && !leg.tc) leg.tc = wall(); });
  state.legs[role] = leg;
  return leg;
}

// Farbton eines empfangenen Bildes in der Mitte
const probe = document.createElement('canvas');
probe.width = 16; probe.height = 9;
function hueOf(v) {
  if (!v.videoWidth) return null;
  const g = probe.getContext('2d', { willReadFrequently: true });
  g.drawImage(v, 0, 0, 16, 9);
  const [r, gg, b] = g.getImageData(12, 6, 1, 1).data; // rechts unten, ausserhalb des Rauschfelds
  const mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
  if (mx - mn < 30) return null;
  let h;
  if (mx === r) h = ((gg - b) / (mx - mn)) % 6;
  else if (mx === gg) h = (b - r) / (mx - mn) + 2;
  else h = (r - gg) / (mx - mn) + 4;
  return Math.round((h * 60 + 360) % 360);
}

// Messwerte fuer den Test
window.__sfuReport = async function () {
  const out = { peer, path, csrc: state.csrc, legs: {}, recv: [], actx: audioCtx && audioCtx.state, src: state.srcKind };
  for (const leg of Object.values(state.legs)) {
    const st = await leg.pc.getStats();
    st.forEach((s) => { if (s.type === 'media-source' && s.kind === 'audio') out.srcLevel = s.audioLevel; });
  }
  for (const [role, leg] of Object.entries(state.legs)) {
    const L = out.legs[role] = { id: leg.id, state: leg.pc.connectionState, t_conn: leg.tc ? Math.round(leg.tc - leg.t0) : null };
    const st = await leg.pc.getStats();
    st.forEach((s) => {
      if (s.type === 'transport' && s.selectedCandidatePairId) L.selPair = s.selectedCandidatePairId;
    });
    st.forEach((s) => {
      if (s.type === 'candidate-pair' && (s.id === L.selPair || (!L.selPair && s.nominated && s.state === 'succeeded'))) { L.rtt = s.currentRoundTripTime; L.avail_out = s.availableOutgoingBitrate; L.bytes_sent = s.bytesSent; L.bytes_recv = s.bytesReceived; L.lpair = s.localCandidateId; }
      if (s.type === 'outbound-rtp') {
        const o = L.out = L.out || [];
        o.push({ kind: s.kind, rid: s.rid, bytes: s.bytesSent, w: s.frameWidth, h: s.frameHeight, fps: s.framesPerSecond, ql: s.qualityLimitationReason, tgt: s.targetBitrate, sent: s.packetsSent, nack: s.nackCount, pli: s.pliCount });
      }
      if (s.type === 'remote-inbound-rtp') { (L.rin = L.rin || []).push({ kind: s.kind, rtt: s.roundTripTime, lost: s.packetsLost, fl: s.fractionLost, jitter: s.jitter }); }
    });
    st.forEach((s) => { if (s.type === 'local-candidate' && s.id === L.lpair) { L.cand = `${s.candidateType}/${s.protocol}/${s.relayProtocol || ''}`; } });
  }
  out.lat = lat;
  for (const r of state.recv) {
    const x = { kind: r.kind, mid: r.mid, csrc: (r.receiver.getContributingSources() || []).map((c) => c.source) };
    const st = await r.receiver.getStats();
    st.forEach((s) => {
      if (s.type !== 'inbound-rtp') return;
      if (r.kind === 'video') {
        x.framesDecoded = s.framesDecoded || 0; x.height = s.frameHeight || 0; x.freezes = s.freezeCount || 0; x.fps = s.framesPerSecond || 0;
        x.jbt = s.jitterBufferTargetDelay || 0; x.jbd = s.jitterBufferDelay || 0; x.jbe = s.jitterBufferEmittedCount || 0; x.jbm = s.jitterBufferMinimumDelay || 0;
        x.freezeDur = s.totalFreezesDuration || 0; x.bytes = s.bytesReceived || 0; x.lost = s.packetsLost || 0; x.recvd = s.packetsReceived || 0; x.width = s.frameWidth || 0;
        x.jitter = s.jitter || 0; x.framesReceived = s.framesReceived || 0; x.framesDropped = s.framesDropped || 0; x.pauseDur = s.totalPausesDuration || 0; x.pauses = s.pauseCount || 0;
        x.firstFrame = r.ff ? Math.round(r.ff - state.t0) : null; x.ts = s.timestamp;
      } else {
        x.samples = s.totalSamplesReceived || 0; x.level = s.audioLevel || 0; x.energy = s.totalAudioEnergy || 0;
        x.concealed = s.concealedSamples || 0; x.concealEv = s.concealmentEvents || 0; x.lost = s.packetsLost || 0; x.recvd = s.packetsReceived || 0;
        x.jitter = s.jitter || 0; x.bytes = s.bytesReceived || 0; x.jbd = s.jitterBufferDelay || 0; x.jbe = s.jitterBufferEmittedCount || 0; x.ts = s.timestamp;
        x.firstBeep = r.ff ? Math.round(r.ff - state.t0) : null;
        x.insSamples = s.insertedSamplesForDeceleration || 0; x.remSamples = s.removedSamplesForAcceleration || 0;
        if (r.an) {
          const f = new Float32Array(r.an.frequencyBinCount);
          r.an.getFloatFrequencyData(f);
          let bi = 0;
          for (let i = 1; i < f.length; i++) if (f[i] > f[bi]) bi = i;
          x.peakHz = Math.round(bi * audioCtx.sampleRate / r.an.fftSize);
          x.peakDb = Math.round(f[bi]);
        }
      }
    });
    if (r.kind === 'video') { x.hue = hueOf(r.el); x.markHue = r.mhue; }
    out.recv.push(x);
  }
  return out;
};

// Zeitmarke im Bild lesen (16 Balken oben) -> Ende-zu-Ende-Latenz je Quelle (Farbton unten rechts)
const mk = document.createElement('canvas');
function readMark(r) {
  const v = r.el;
  if (!v || !v.videoWidth) return;
  const w = v.videoWidth, h = v.videoHeight;
  mk.width = w; mk.height = h;
  const g = mk.getContext('2d', { willReadFrequently: true });
  g.drawImage(v, 0, 0, w, h);
  const row = g.getImageData(0, Math.round(h / 18), w, 1).data;
  let t = 0;
  for (let b = 0; b < 16; b++) {
    const x = Math.round((b + 0.5) * w / 16);
    t = (t << 1) | (row[x * 4] > 127 ? 1 : 0);
  }
  if (r.lastMark === t) return; // gleiches Bild
  r.lastMark = t;
  const now = Math.round(wall()) & 0xffff;
  const d = (now - t + 65536) % 65536;
  if (d > 5000) return;
  if (!r.ff) r.ff = wall();
  const hu = hueOf(v); r.mhue = hu;
  const k = String(hu == null ? '?' : (Math.round(hu / 10) * 10) % 360);
  (lat.video[k] = lat.video[k] || []).push(d);
  if (lat.video[k].length > 4000) lat.video[k].shift();
}
// Pieps-Erkennung: Beginn eines Pieps nach >= 300 ms Stille -> Abstand zur vollen Sekunde
function beepDetector(srcN, r) {
  const sp = audioCtx.createScriptProcessor(256, 1, 1);
  let quiet = 0, first = true;
  sp.onaudioprocess = (ev) => {
    const d = ev.inputBuffer.getChannelData(0);
    let e = 0;
    for (let i = 0; i < d.length; i++) e += d[i] * d[i];
    const rms = Math.sqrt(e / d.length);
    const now = wall();
    if (rms > 0.02) {
      if (quiet > 0.3) {
        if (!r.ff) r.ff = now;
        // Position im Block grob: Blockende = jetzt - Ausgabelatenz der Verarbeitung; ohne Korrektur (gleich fuer alle Wege)
        const ms = Math.round(now % 1000);
        const k = 'a' + r.mid;
        (lat.audio[k] = lat.audio[k] || []).push(ms > 900 ? ms - 1000 : ms);
        if (lat.audio[k].length > 2000) lat.audio[k].shift();
      }
      quiet = 0;
    } else quiet += d.length / audioCtx.sampleRate;
    if (first) first = false;
  };
  srcN.connect(sp);
  const sink = audioCtx.createGain(); sink.gain.value = 0;
  sp.connect(sink).connect(audioCtx.destination);
}
if (mark) setInterval(() => { for (const r of state.recv) if (r.kind === 'video') readMark(r); }, 15);

async function main() {
  state.t0 = wall();
  $('who').textContent = `${peer} · ${path}`;
  startSources();
  await unlockCandidates();
  await api('POST', '/api/dev/room', { room });
  let turn = null;
  if (path === 'turn') {
    const tok = (await api('POST', '/api/dev/token', { room, peer, role: 'audio' })).token;
    turn = await api('POST', '/api/sfu/turn', { token: tok });
  }
  await openLeg('audio', turn);
  await openLeg('video', turn);
  state.ready = true;
  $('state').textContent = 'bereit';
}
main().catch((e) => { state.error = String(e); $('state').textContent = 'Fehler: ' + e; });
