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
      g.fillRect(x + (i % 8) * 15, 20 + ((i / 8) | 0) * 15, 15, 15);
    }
  }, 1000 / 30);
  srcVideo = cv.captureStream(30).getVideoTracks()[0];
  audioCtx = new AudioContext();
  const osc = audioCtx.createOscillator();
  osc.frequency.value = 200 + hue;
  gain = audioCtx.createGain();
  gain.gain.value = loud ? 0.6 : 0.05;
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
    cfg.iceServers = [{ urls: `turns:${location.hostname}:${location.port || 443}?transport=tcp`, username: turn.username, credential: turn.credential }];
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
    const r = { kind: ev.track.kind, mid: ev.transceiver.mid, receiver: ev.receiver, el: null };
    try { if ('jitterBufferTarget' in ev.receiver && ev.track.kind === 'video') ev.receiver.jitterBufferTarget = jbTarget; } catch (e) { /* alt */ }
    const ms = new MediaStream([ev.track]);
    if (ev.track.kind === 'audio') {
      const a = new Audio(); a.srcObject = ms; a.volume = 0; a.play().catch(() => {}); r.el = a;
      // Analyse: Pegel und Spitzenfrequenz (Absender = 200 + Farbton Hz)
      try {
        r.an = audioCtx.createAnalyser();
        r.an.fftSize = 8192;
        audioCtx.createMediaStreamSource(ms).connect(r.an);
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
  const res = await api('POST', '/api/sfu/leg', { token: tok, path, offer: pc.localDescription.sdp, audio: role === 'audio' ? 3 : 0, speaker: role === 'video', tiles: role === 'video' ? tiles : 0 });
  leg.id = res.id;
  state.csrc = res.csrc;
  await pc.setRemoteDescription({ type: 'answer', sdp: res.answer });
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
    out.legs[role] = { id: leg.id, state: leg.pc.connectionState };
  }
  for (const r of state.recv) {
    const x = { kind: r.kind, mid: r.mid, csrc: (r.receiver.getContributingSources() || []).map((c) => c.source) };
    const st = await r.receiver.getStats();
    st.forEach((s) => {
      if (s.type !== 'inbound-rtp') return;
      if (r.kind === 'video') {
        x.framesDecoded = s.framesDecoded || 0; x.height = s.frameHeight || 0; x.freezes = s.freezeCount || 0; x.fps = s.framesPerSecond || 0;
      } else {
        x.samples = s.totalSamplesReceived || 0; x.level = s.audioLevel || 0; x.energy = s.totalAudioEnergy || 0;
        x.concealed = s.concealedSamples || 0;
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
    if (r.kind === 'video') x.hue = hueOf(r.el);
    out.recv.push(x);
  }
  return out;
};

async function main() {
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
