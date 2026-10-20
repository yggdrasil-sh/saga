// Spike D3 (Saga 2.5, nur Bewertung, docs/APP-API.md "Ausblick 2.5"): Frame-E2E im Browser - RTCRtpScriptTransform + AES-GCM je
// Frame (Trailer IV + Schluessel-ID, Kopf unverschluesselt). SAGA_BASE=http://host:port node tools/perf/spike_frame_e2e.js
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const BASE = process.env.SAGA_BASE;
const W = `
      let key = null, n = 0, tEnc = 0, tDec = 0, nEnc = 0, nDec = 0, bad = 0;
      const clear = (f) => f.type === 'key' ? 10 : f.type === 'delta' ? 3 : 1;      // VP8-Kopf bzw. Opus-TOC unverschluesselt
      async function enc(f, c) {
        const t0 = performance.now(), d = new Uint8Array(f.data), h = clear(f), iv = new Uint8Array(12); new DataView(iv.buffer).setUint32(8, ++n);
        const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: d.subarray(0, h) }, key, d.subarray(h)));
        const o = new Uint8Array(h + ct.length + 13); o.set(d.subarray(0, h)); o.set(ct, h); o.set(iv, h + ct.length); o[o.length - 1] = 1;   // Schluessel-ID 1
        f.data = o.buffer; tEnc += performance.now() - t0; nEnc++; c.enqueue(f);
      }
      async function dec(f, c) {
        const t0 = performance.now(), d = new Uint8Array(f.data), h = clear(f);
        try {
          const iv = d.subarray(d.length - 13, d.length - 1);
          const pt = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv, additionalData: d.subarray(0, h) }, key, d.subarray(h, d.length - 13)));
          const o = new Uint8Array(h + pt.length); o.set(d.subarray(0, h)); o.set(pt, h); f.data = o.buffer; c.enqueue(f);
        } catch (e) { bad++; }
        tDec += performance.now() - t0; nDec++;
      }
      onrtctransform = (ev) => { const t = ev.transformer, side = t.options.side;
        t.readable.pipeThrough(new TransformStream({ transform: side === 'send' ? enc : dec })).pipeTo(t.writable); };
      onmessage = async (m) => { if (m.data.key) key = await crypto.subtle.importKey('raw', m.data.key, 'AES-GCM', false, ['encrypt', 'decrypt']);
        if (m.data.stats) postMessage({ nEnc, nDec, bad, encMs: nEnc ? tEnc / nEnc : 0, decMs: nDec ? tDec / nDec : 0 }); };`;
(async () => {
  const br = await chromium.launch({ channel: process.env.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + new URL(BASE).origin] });
  const ctx = await br.newContext(); await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage(); p.on('console', m => console.log('  [c] ' + m.text())); p.on('pageerror', e => console.log('  [err] ' + e.message)); await p.route(BASE + '/spike', r => r.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>spike</title>' }));
  await p.route(BASE + '/spike-worker.js', r => r.fulfill({ status: 200, contentType: 'text/javascript', body: W }));
  await p.goto(BASE + '/spike');
  const out = await p.evaluate(async () => {
    const feat = { scriptTransform: typeof RTCRtpScriptTransform === 'function', encodedStreams: typeof RTCRtpSender.prototype.createEncodedStreams === 'function',
                   ua: navigator.userAgent.replace(/.*(Chrome\/[\d.]+).*/, '$1') };

    async function run(e2e) {
      const w = e2e ? new Worker('/spike-worker.js') : null;
      if (w) w.postMessage({ key: crypto.getRandomValues(new Uint8Array(16)).buffer });
      const a = new RTCPeerConnection(), b = new RTCPeerConnection();
      a.onicecandidate = e => e.candidate && b.addIceCandidate(e.candidate); b.onicecandidate = e => e.candidate && a.addIceCandidate(e.candidate);
      const s = await navigator.mediaDevices.getUserMedia({ audio: true, video: { width: 1280, height: 720 } });
      s.getTracks().forEach(t => { const snd = a.addTrack(t, s); if (w) snd.transform = new RTCRtpScriptTransform(w, { side: 'send' }); });
      b.ontrack = e => { if (w) e.receiver.transform = new RTCRtpScriptTransform(w, { side: 'recv' }); };
      await a.setLocalDescription(); await b.setRemoteDescription(a.localDescription); await b.setLocalDescription(); await a.setRemoteDescription(b.localDescription);
      console.log('verbunden? ' + e2e); await new Promise(r => setTimeout(r, 8000));
      let fd = 0, ad = 0, kb = 0;
      (await b.getStats()).forEach(x => { if (x.type === 'inbound-rtp' && x.kind === 'video') { fd = x.framesDecoded; kb = Math.round(x.bytesReceived * 8 / 8000); }
                                          if (x.type === 'inbound-rtp' && x.kind === 'audio') ad = x.totalSamplesReceived; });
      console.log('stats ' + e2e + ' fd=' + fd); const st = w ? await Promise.race([new Promise(r => { w.onmessage = m => r(m.data); w.postMessage({ stats: 1 }); }), new Promise(r => setTimeout(() => r('timeout'), 3000))]) : null;
      s.getTracks().forEach(t => t.stop()); a.close(); b.close(); if (w) w.terminate();
      return { framesDecoded: fd, audioSamples: ad, kbps: kb, worker: st };
    }
    if (!feat.scriptTransform) return { feat };
    return { feat, plain: await run(false), e2e: await run(true) };
  });
  console.log('SPIKE ' + JSON.stringify(out));
  await br.close();
})();
