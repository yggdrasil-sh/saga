// Spike SP2 (Saga 2.5, Konzept Chat B3 Sprachnachrichten, nur Bewertung): Opus/Ogg im Browser aufnehmen und abspielen.
// Prueft je Browser: MediaRecorder.isTypeSupported (ogg/webm opus), echte Aufnahme 2 s aus WebAudio (Kopf OggS bzw. EBML,
// Groesse bei 24 kbit/s), canPlayType, Abspielen der eigenen Aufnahme (<audio> + decodeAudioData), WebCodecs AudioEncoder/
// AudioDecoder opus (48 kHz mono 24 kbit/s, 20-ms-Rahmen). Ohne Server: Seite ueber page.route (https, sicherer Kontext).
//   PLAYWRIGHT=/opt/npm-tools/node_modules/playwright PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node tools/perf/spike_opus.js
// SPIKE_FILE=<datei.ogg>: zusaetzlich eine fremde Ogg-Opus-Datei abspielen (z. B. aus GStreamer, wie die Linux-App).
// Browser: SPIKE_BROWSERS="chromium firefox webkit" (fehlende werden uebersprungen); Kanal fuer Chromium: PW_CHANNEL.
// Stand 20.10.2026 (Chromium 141 headless, Firefox/WebKit hier nicht installiert): MediaRecorder kann kein audio/ogg, aber
// audio/webm;codecs=opus (~25 kbit/s bei 24000, Kopf EBML, Dauer im <audio> Infinity - WebM ohne Cues); WebCodecs
// AudioEncoder/AudioDecoder opus ja (50 Pakete/s, ~26 kbit/s); canPlayType ogg opus "probably"; selbst gemuxtes Ogg
// (oggOpus unten) spielt mit richtiger Dauer, ebenso eine Datei aus GStreamer (opusenc ! oggmux) - ueber eine Antwort ohne
// Range zeigt <audio> dann aber Dauer Infinity (Dauer daher aus den Metadaten "dur" nehmen, Range ueber saga-media).
'use strict';
const pw =require(process.env.PLAYWRIGHT || 'playwright-core');
const PAGE = '<!doctype html><meta charset="utf-8"><title>opus</title><body>opus</body>';

// Ogg-Kapselung fuer Opus-Pakete (RFC 3533/7845): OpusHead, OpusTags, Datenseiten mit Granule = Samples inkl. pre-skip
function oggOpus(pkts, spp, preskip) {
  const T = new Uint32Array(256);
  for (let i = 0; i < 256; i++) { let r = i << 24; for (let j = 0; j < 8; j++) r = r & 0x80000000 ? (r << 1) ^ 0x04c11db7 : r << 1; T[i] = r >>> 0; }
  const crc = (b) => { let c = 0; for (let i = 0; i < b.length; i++) c = ((c << 8) ^ T[((c >>> 24) ^ b[i]) & 255]) >>> 0; return c; };
  const pages = []; let seq = 0;
  const page = (packets, granule, flags) => {
    const lace = []; for (const p of packets) { let n = p.length; while (n >= 255) { lace.push(255); n -= 255; } lace.push(n); }
    const len = packets.reduce((n, p) => n + p.length, 0), b = new Uint8Array(27 + lace.length + len), dv = new DataView(b.buffer);
    b.set([0x4f, 0x67, 0x67, 0x53, 0, flags]); dv.setBigUint64(6, BigInt(granule), true); dv.setUint32(14, 0x5a6a, true); dv.setUint32(18, seq++, true);
    b[26] = lace.length; b.set(lace, 27); let o = 27 + lace.length; for (const p of packets) { b.set(p, o); o += p.length; }
    dv.setUint32(22, crc(b), true); pages.push(b);
  };
  const head = new Uint8Array(19), hd = new DataView(head.buffer);
  head.set(new TextEncoder().encode('OpusHead')); head[8] = 1; head[9] = 1; hd.setUint16(10, preskip, true); hd.setUint32(12, 48000, true);
  page([head], 0, 2);
  const vendor = new TextEncoder().encode('saga-spike'), tags = new Uint8Array(8 + 4 + vendor.length + 4), td = new DataView(tags.buffer);
  tags.set(new TextEncoder().encode('OpusTags')); td.setUint32(8, vendor.length, true); tags.set(vendor, 12);
  page([tags], 0, 0);
  let g = 0;
  for (let i = 0; i < pkts.length; i += 50) {                                   // 50 Pakete (1 s) je Seite
    const part = pkts.slice(i, i + 50); g += part.length * spp;
    page(part, g, i + 50 >= pkts.length ? 4 : 0);
  }
  const all = new Uint8Array(pages.reduce((n, p) => n + p.length, 0)); let o = 0; for (const p of pages) { all.set(p, o); o += p.length; }
  return all;
}

async function probe() {
  const out = { ua: navigator.userAgent, secure: isSecureContext };
  const MR = typeof MediaRecorder !== 'undefined' ? MediaRecorder : null;
  const types = ['audio/ogg;codecs=opus', 'audio/ogg', 'audio/webm;codecs=opus', 'audio/webm', 'audio/mp4;codecs=opus', 'audio/mp4'];
  out.mr = {};
  for (const t of types) out.mr[t] = MR ? MR.isTypeSupported(t) : null;
  const a = document.createElement('audio');
  out.play = {};
  for (const t of ['audio/ogg; codecs=opus', 'audio/ogg', 'audio/webm; codecs=opus', 'audio/mp4; codecs=opus', 'audio/mpeg']) out.play[t] = a.canPlayType(t);
  // echte Aufnahme 2 s: Oszillator -> MediaStream -> MediaRecorder
  out.rec = {};
  for (const t of ['audio/ogg;codecs=opus', 'audio/webm;codecs=opus']) {
    if (!MR || !MR.isTypeSupported(t)) { out.rec[t] = 'nicht unterstuetzt'; continue; }
    try {
      const ac = new AudioContext({ sampleRate: 48000 }), osc = ac.createOscillator(), dst = ac.createMediaStreamDestination();
      osc.frequency.value = 440; osc.connect(dst); osc.start();
      const mr = new MR(dst.stream, { mimeType: t, audioBitsPerSecond: 24000 }), parts = [];
      mr.ondataavailable = (e) => parts.push(e.data);
      const done = new Promise((r) => { mr.onstop = r; });
      mr.start(500); await new Promise((r) => setTimeout(r, 2000)); mr.stop(); await done; osc.stop();
      const blob = new Blob(parts, { type: t }), buf = new Uint8Array(await blob.arrayBuffer());
      const head = Array.from(buf.subarray(0, 4)).map((x) => x.toString(16).padStart(2, '0')).join('');
      const r = { bytes: buf.length, kbit_s: Math.round(buf.length * 8 / 2 / 100) / 10, head, ogg: head === '4f676753', ebml: head === '1a45dfa3', mime: mr.mimeType };
      // Abspielen der eigenen Aufnahme: <audio> (Metadaten, Dauer) und WebAudio-Dekoder
      const url = URL.createObjectURL(blob), el = new Audio();
      r.audio = await new Promise((res) => { el.onloadedmetadata = () => res({ ok: true, duration: el.duration }); el.onerror = () => res({ ok: false, err: el.error && el.error.code }); el.src = url; setTimeout(() => res({ ok: false, err: 'timeout' }), 4000); });
      try { const ab = await ac.decodeAudioData(buf.buffer.slice(0)); r.decode = { ok: true, duration: Math.round(ab.duration * 100) / 100, rate: ab.sampleRate }; }
      catch (e) { r.decode = { ok: false, err: String(e && e.name) }; }
      ac.close();
      out.rec[t] = r;
    } catch (e) { out.rec[t] = 'Fehler: ' + e; }
  }
  // WebCodecs: Opus kodieren und wieder dekodieren (ohne Container; Ogg-Mux waere eigener Code bzw. WASM)
  out.wc = {};
  if (typeof AudioEncoder === 'undefined') out.wc = { encoder: 'fehlt' };
  else {
    const cfg = { codec: 'opus', sampleRate: 48000, numberOfChannels: 1, bitrate: 24000, opus: { frameDuration: 20000, application: 'voip' } };
    try {
      const s = await AudioEncoder.isConfigSupported(cfg);
      out.wc.encoder = !!s.supported;
      if (s.supported) {
        const chunks = []; let desc = null;
        const enc = new AudioEncoder({ output: (c, m) => { chunks.push(c); if (m && m.decoderConfig) desc = m.decoderConfig; }, error: (e) => { out.wc.encErr = String(e); } });
        enc.configure(cfg);
        const N = 48000, pcm = new Float32Array(N);
        for (let i = 0; i < N; i++) pcm[i] = 0.3 * Math.sin(2 * Math.PI * 440 * i / 48000);
        for (let o = 0; o < N; o += 960) enc.encode(new AudioData({ format: 'f32', sampleRate: 48000, numberOfFrames: 960, numberOfChannels: 1, timestamp: o * 1e6 / 48000, data: pcm.subarray(o, o + 960) }));
        await enc.flush();
        out.wc.frames = chunks.length; out.wc.bytes = chunks.reduce((n, c) => n + c.byteLength, 0);
        out.wc.kbit_s = Math.round(out.wc.bytes * 8 / 100) / 10;
        if (typeof AudioDecoder !== 'undefined') {
          const dc = desc || { codec: 'opus', sampleRate: 48000, numberOfChannels: 1 };
          const ds = await AudioDecoder.isConfigSupported(dc);
          out.wc.decoder = !!ds.supported;
          if (ds.supported) {
            let n = 0;
            const dec = new AudioDecoder({ output: (d) => { n += d.numberOfFrames; d.close(); }, error: (e) => { out.wc.decErr = String(e); } });
            dec.configure(dc);
            for (const c of chunks) dec.decode(c);
            await dec.flush();
            out.wc.decoded_s = Math.round(n / 48000 * 100) / 100;
          }
        } else out.wc.decoder = 'fehlt';
        // eigener kleiner Ogg-Muxer (RFC 7845) fuer die WebCodecs-Pakete -> spielt der Browser das als audio/ogg ab?
        const pkts = chunks.map((c) => { const b = new Uint8Array(c.byteLength); c.copyTo(b); return b; });
        const ogg = oggOpus(pkts, 960, 312), blob = new Blob([ogg], { type: 'audio/ogg; codecs=opus' }), el = new Audio();
        out.ogg_mux = { bytes: ogg.length };
        out.ogg_mux.audio = await new Promise((res) => { el.onloadedmetadata = () => res({ ok: true, duration: Math.round(el.duration * 100) / 100 });
          el.onerror = () => res({ ok: false, err: el.error && el.error.code }); el.src = URL.createObjectURL(blob); setTimeout(() => res({ ok: false, err: 'timeout' }), 4000); });
        try { const ac2 = new AudioContext(); const ab = await ac2.decodeAudioData(ogg.buffer.slice(0)); out.ogg_mux.decode = { ok: true, duration: Math.round(ab.duration * 100) / 100 }; ac2.close(); }
        catch (e) { out.ogg_mux.decode = { ok: false, err: String(e && e.name) }; }
      }
    } catch (e) { out.wc.err = String(e); }
  }
  out.wasm = typeof WebAssembly === 'object';
  return out;
}

(async () => {
  const want = (process.env.SPIKE_BROWSERS || 'chromium firefox webkit').split(/\s+/).filter(Boolean);
  const all = {};
  for (const name of want) {
    let br;
    try {
      br = await pw[name].launch(Object.assign({ headless: true }, name === 'chromium' ? { channel: process.env.PW_CHANNEL || undefined,
        args: ['--autoplay-policy=no-user-gesture-required'] } : {}));
    } catch (e) { all[name] = 'nicht installiert (' + String(e.message || e).split('\n')[0].slice(0, 120) + ')'; continue; }
    try {
      const page = await br.newPage();
      await page.route('https://spike.test/**', (r) => r.fulfill({ status: 200, contentType: 'text/html', body: PAGE }));
      await page.goto('https://spike.test/');
      await page.addScriptTag({ content: oggOpus.toString() });
      all[name] = Object.assign({ version: br.version() }, await page.evaluate(probe));
      if (process.env.SPIKE_FILE) {                    // fremde Ogg-Datei (z. B. GStreamer, Linux-App) abspielbar?
        await page.route('https://spike.test/f.ogg', (r) => r.fulfill({ status: 200, contentType: 'audio/ogg', body: require('fs').readFileSync(process.env.SPIKE_FILE) }));
        all[name].file = await page.evaluate(async () => {
          const el = new Audio(), r = {};
          r.audio = await new Promise((res) => { el.onloadedmetadata = () => res({ ok: true, duration: Math.round(el.duration * 100) / 100 });
            el.onerror = () => res({ ok: false, err: el.error && el.error.code }); el.src = '/f.ogg'; setTimeout(() => res({ ok: false, err: 'timeout' }), 4000); });
          try { const ab = await new AudioContext().decodeAudioData(await (await fetch('/f.ogg')).arrayBuffer()); r.decode = { ok: true, duration: Math.round(ab.duration * 100) / 100 }; }
          catch (e) { r.decode = { ok: false, err: String(e && e.name) }; }
          return r;
        });
      }
    } catch (e) { all[name] = 'Fehler: ' + e; }
    await br.close();
  }
  console.log(JSON.stringify(all, null, 1));
})();
