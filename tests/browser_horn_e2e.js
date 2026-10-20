// Browser-Test Frame-E2E Gjallarhorn (saga-horn/v1, static/frame-e2e.js, docs/HORN-E2E.md) im headless Chromium:
//   SAGA_BASE=http://host:port [PLAYWRIGHT=/pfad/zu/playwright] [PW_CHANNEL=chromium] node tests/browser_horn_e2e.js
// Die Seite ist /login von Saga (echte CSP mit worker-src 'self'), der Worker ist die statische Datei /static/frame-e2e.js.
// Geprueft: Testvektoren tests/vectors/horn_e2e.json IM Worker (gleicher Kern wie Node/Python); Blob-Worker von der CSP
// abgewiesen; Loopback zweier PeerConnections (Video 1280x720 aus einem Canvas mit Rauschen, VP8 erzwungen, Ton Opus):
// ohne Sendeschluessel geht nichts raus, ohne Empfangsschluessel wird nichts dekodiert (kein Klartext durchgereicht),
// mit Schluessel Bild und Ton; Ratsche (Beitritt) ohne fkey an den Empfaenger, Neuschluessel (Verlassen) mit Umschalten
// nach 1,5 s, Neuschluessel ohne Zustellung -> nichts lesbar + need, Nachlieferung -> wieder Bild; Rueckfall
// createEncodedStreams; Leistung je Video-Rahmen bei 720p (Ziel <= 0,1 ms, PERF-Zeile). WebRTC braucht eine Netz-
// Schnittstelle ausser lo.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE) { console.error('SAGA_BASE setzen'); process.exit(2); }
const V = JSON.parse(fs.readFileSync(path.join(__dirname, 'vectors', 'horn_e2e.json'), 'utf8'));
const res = [], PERF = {};
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }

(async () => {
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] });
  try {
    const ctx = await browser.newContext();
    await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
    const p = await ctx.newPage();
    p.on('pageerror', e => console.log('  [pageerror] ' + e.message));
    if (E.DEBUG) p.on('console', m => console.log('  [console] ' + m.text()));
    const r = await p.goto(BASE + '/login');
    const csp = (r && r.headers()['content-security-policy']) || '';
    ok('Seite mit CSP worker-src \'self\'', /worker-src 'self'(;|$)/.test(csp), csp);
    await p.addScriptTag({ url: '/static/frame-e2e.js' });
    ok('window.SagaFrameE2E geladen, Weg ' + await p.evaluate(() => window.SagaFrameE2E.supported()),
       await p.evaluate(() => !!window.SagaFrameE2E && !!window.SagaFrameE2E.supported()));

    // --- Vektoren im Worker ------------------------------------------------------------------------------------------
    const st = await p.evaluate(async (v) => { const x = window.SagaFrameE2E.create(); const r = await x.selftest(v); x.close(); return r; }, V);
    ok(`Testvektoren im Worker (${st.n} Pruefungen)`, st.ok, st.fails);
    const blob = await p.evaluate(() => new Promise((ok) => {
      try {
        const w = new Worker(URL.createObjectURL(new Blob(['postMessage(1)'], { type: 'text/javascript' })));
        w.onmessage = () => ok('lief'); w.onerror = () => ok('abgewiesen'); setTimeout(() => ok('abgewiesen (still)'), 1500);
      } catch (e) { ok('abgewiesen (' + e.name + ')'); }
    }));
    ok('CSP: Blob-Worker abgewiesen (' + blob + ')', blob !== 'lief');

    // --- Loopback mit RTCRtpScriptTransform bzw. createEncodedStreams ------------------------------------------------
    await p.evaluate(() => {
      const X = window.SagaFrameE2E;
      const W = 1280, H = 720;
      window.T = {};
      // Kamera-Ersatz 1280x720: ruhiger Hintergrund, bewegter verrauschter Bereich (wie ein Gesicht) und ein Balken -
      // VP8 bei 1,5 Mbit/s (Tabelle "direct") gibt damit Rahmen von einigen KB wie bei einer echten Kamera.
      // noisy: ganzes Bild Rauschen (schlimmster Fall: sehr grosse Rahmen, wenige Bilder je Sekunde).
      window.mkCanvas = (noisy) => {
        const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
        const g = cv.getContext('2d'), bw = noisy ? W : 320, bh = noisy ? H : 360;
        const img = g.createImageData(bw, bh), u32 = new Uint32Array(img.data.buffer);
        let s = 0x12345678, x = 0;
        const grad = g.createLinearGradient(0, 0, W, H); grad.addColorStop(0, '#234'); grad.addColorStop(1, '#a74');
        const draw = () => {
          g.fillStyle = grad; g.fillRect(0, 0, W, H);
          for (let i = 0; i < u32.length; i += noisy ? 3 : 1) { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; u32[i] = (s & 0x3f3f3f) | 0xff606060; }
          x = (x + 12) % (W - bw + 1);
          g.putImageData(img, noisy ? 0 : x, noisy ? 0 : 180); g.fillStyle = '#c90'; g.fillRect((x * 2) % W, 0, 40, H);
        };
        draw(); window.T.iv = setInterval(draw, 33);
        return cv.captureStream(30).getVideoTracks()[0];
      };
      window.mkPair = async (mode, video, noisy) => {
        const opt = mode === 'streams' ? { encodedInsertableStreams: true } : {};
        const A = X.create({ mode }), B = X.create({ mode });
        const pa = new RTCPeerConnection(opt), pb = new RTCPeerConnection(opt);
        pa.onicecandidate = (e) => e.candidate && pb.addIceCandidate(e.candidate);
        pb.onicecandidate = (e) => e.candidate && pa.addIceCandidate(e.candidate);
        const mic = (await navigator.mediaDevices.getUserMedia({ audio: true })).getAudioTracks()[0];
        const tracks = [mic].concat(video ? [window.mkCanvas(noisy)] : []);
        const ms = new MediaStream(tracks);
        tracks.forEach((t) => {
          const tr = pa.addTransceiver(t, { direction: 'sendonly', streams: [ms] });
          A.sender(tr.sender, t.kind);
          if (t.kind === 'video') {
            const c = RTCRtpReceiver.getCapabilities('video').codecs;
            tr.setCodecPreferences(c.filter((x) => /vp8/i.test(x.mimeType)).concat(c.filter((x) => !/vp8/i.test(x.mimeType))));
          }
        });
        pb.ontrack = (e) => B.receiver(e.receiver, e.track.kind, 'devA');
        await pa.setLocalDescription(); await pb.setRemoteDescription(pa.localDescription);
        await pb.setLocalDescription(); await pa.setRemoteDescription(pb.localDescription);
        const vs = pa.getSenders().find((x) => x.track && x.track.kind === 'video');
        if (vs) { const pr = vs.getParameters(); pr.degradationPreference = 'maintain-resolution';
                  (pr.encodings || []).forEach((e) => { e.maxBitrate = noisy ? 2500000 : 1500000; }); await vs.setParameters(pr).catch(() => {}); }
        const need = []; B.on('need', (d) => need.push(d));
        return { A, B, pa, pb, need };
      };
      window.inb = async (pc) => {
        const o = { fd: 0, w: 0, h: 0, as: 0, fr: 0 };
        (await pc.getStats()).forEach((x) => {
          if (x.type === 'inbound-rtp' && x.kind === 'video') { o.fd = x.framesDecoded || 0; o.w = x.frameWidth || 0; o.h = x.frameHeight || 0; o.fr = x.framesReceived || 0; }
          if (x.type === 'inbound-rtp' && x.kind === 'audio') o.as = x.packetsReceived || 0;   // Ausgabe fehlt headless: Pakete
        });
        return o;
      };
      window.outb = async (pc) => {
        const o = {};
        (await pc.getStats()).forEach((x) => { if (x.type === 'outbound-rtp' && x.kind === 'video') { o.fe = (o.fe || 0) + (x.framesEncoded || 0); o.bytes = (o.bytes || 0) + (x.bytesSent || 0); o.codec = x.codecId; o.w = x.frameWidth; } });
        return o;
      };
      window.sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    });
    const until = async (fn, ms = 10000) => { const t = Date.now(); while (Date.now() - t < ms) { const v = await fn(); if (v) return v; await p.waitForTimeout(250); } return null; };

    const mode = await p.evaluate(() => window.SagaFrameE2E.supported());
    await p.evaluate(async (m) => { window.P = await window.mkPair(m, true); }, mode);
    await p.waitForTimeout(3000);
    let a = await p.evaluate(async () => ({ a: await P.A.stats(true), b: await P.B.stats(true), i: await inb(P.pb) }));
    ok('ohne Sendeschluessel: nichts gesendet (drop nokey beim Sender, Empfaenger dekodiert nichts)',
       (a.a.drop.nokey || 0) > 10 && !Object.keys(a.a.enc).length && a.i.fd === 0, a);
    const k0 = await p.evaluate(async () => { const k = await P.A.own('start'); window.K0 = k; return { n: k.n, kid: k.kid, s: k.s }; });
    ok('A: eigener Schluessel n=0', k0.n === 0 && k0.kid === 0);
    await p.waitForTimeout(2500);
    a = await p.evaluate(async () => ({ a: await P.A.stats(true), b: await P.B.stats(true), i: await inb(P.pb) }));
    ok('Empfaenger ohne Schluessel: alles verworfen, kein Bild dekodiert (kein Klartext)',
       (a.b.drop.nokey || 0) > 10 && !Object.keys(a.b.dec).length && a.i.fd === 0 && a.a.enc['video:key'], { drop: a.b.drop, fd: a.i.fd });
    await p.evaluate(async () => { await P.B.peer('devA', K0.n, K0.key, K0.s); });
    const dec1 = await until(async () => { const i = await p.evaluate(() => inb(P.pb)); return i.fd > 30 && i.as > 0 && i; }, 15000);
    const sa = await p.evaluate(() => P.B.stats());
    ok('mit Schluessel: Bild und Ton (' + (dec1 ? dec1.w + 'x' + dec1.h + ', ' + dec1.fd + ' Bilder' : '-') + ', Ton ' + ((sa.dec.audio || {}).n || 0) + ' Rahmen entschluesselt)',
       dec1 && dec1.w === 1280 && dec1.h === 720 && (sa.dec.audio || {}).n > 10, { dec1, sa });

    // Ratsche (Beitritt): B bekommt kein fkey und liest weiter
    let fd0 = (await p.evaluate(() => inb(P.pb))).fd;
    await p.evaluate(async () => { await P.B.stats(true); window.K1 = await P.A.own('ratchet'); });
    await p.waitForTimeout(2000);
    a = await p.evaluate(async () => ({ b: await P.B.stats(true), i: await inb(P.pb) }));
    ok(`Ratsche ohne fkey: B leitet selbst ab, kid ${a.b.kid.recv.devA}, keine Fehler`, a.b.kid.recv.devA === 1 && !a.b.drop.auth && !a.b.drop.nokey && a.i.fd > fd0 + 20,
       { kid: a.b.kid, drop: a.b.drop, fd: a.i.fd - fd0 });

    // Neuschluessel (Verlassen): fkey an B, Umschalten nach 1,5 s, alter Schluessel bis dahin
    fd0 = a.i.fd;
    const sw = await p.evaluate(async () => {
      const k = await P.A.own('rekey'); await P.B.peer('devA', k.n, k.key, k.s);
      await sleep(700); const s1 = (await P.A.stats()).kid.send;
      await sleep(1300); const s2 = (await P.A.stats()).kid.send;
      return { n: k.n, s1, s2 };
    });
    a = await p.evaluate(async () => ({ b: await P.B.stats(true), i: await inb(P.pb) }));
    ok(`Neuschluessel: nach 0,7 s noch kid ${sw.s1}, nach 2 s kid ${sw.s2}; B ohne Luecke`, sw.n === 2 && sw.s1 === 1 && sw.s2 === 2 && !a.b.drop.auth
       && !a.b.drop.nokey && a.i.fd > fd0 + 30, { sw, drop: a.b.drop, fd: a.i.fd - fd0 });

    // Neuschluessel ohne Zustellung: nichts mehr lesbar, need; Nachlieferung -> wieder Bild
    await p.evaluate(async () => { window.K3 = await P.A.own('rekey'); });
    await p.waitForTimeout(4500);
    a = await p.evaluate(async () => ({ b: await P.B.stats(true), i: await inb(P.pb), need: P.need.slice() }));
    fd0 = a.i.fd;
    await p.waitForTimeout(1000);
    const fdStill = (await p.evaluate(() => inb(P.pb))).fd;
    ok(`Neuschluessel nicht zugestellt: verworfen (auth ${a.b.drop.auth || 0}), kein neues Bild, need gemeldet`,
       (a.b.drop.auth || 0) > 10 && fdStill - fd0 <= 1 && a.need.length >= 1 && a.need[0].from === 'devA', { drop: a.b.drop, need: a.need, fd: fdStill - fd0 });
    await p.evaluate(async () => { const [k] = await P.A.ownKeys(); await P.B.peer('devA', k.n, k.key, k.s); });
    const back = await until(async () => (await p.evaluate(() => inb(P.pb))).fd > fdStill + 20, 10000);
    ok('nach Nachlieferung (need -> fkey) wieder Bild', !!back);

    // Leistung: reine Kryptografie im ruhenden Worker (massgeblich fuer das Ziel 0,1 ms), dann im laufenden Anruf
    const bn = await p.evaluate(async () => { const x = window.SagaFrameE2E.create(), out = {};
      for (const sz of [100, 6000, 30000]) out[sz] = await new Promise((ok) => { x.worker.addEventListener('message', function h(m) {
        if (m.data.id === 77) { x.worker.removeEventListener('message', h); ok(m.data.bench); } }); x.worker.postMessage({ op: 'bench', id: 77, size: sz, n: 800 }); });
      x.close(); return out; });
    PERF.bench = Object.fromEntries(Object.entries(bn).map(([k, v]) => [k, { enc: +v.enc_ms.toFixed(4), dec: +v.dec_ms.toFixed(4), enc_par: +v.enc_parallel_ms.toFixed(4) }]));
    // Ziel 0,1 ms gilt fuer eine ruhende Maschine (PERF.ziel_0_1ms); harte Grenze 0,5 ms, damit belastete Testrechner nicht kippen
    PERF.ziel_0_1ms = PERF.bench[6000].enc <= 0.1 && PERF.bench[6000].dec <= 0.1;
    ok(`Worker-Messung 6 KB (720p-Rahmen): ${PERF.bench[6000].enc} ms verschluesseln, ${PERF.bench[6000].dec} ms entschluesseln (Ziel <= 0,1 ms, Grenze 0,5 ms)`,
       PERF.bench[6000].enc <= 0.5 && PERF.bench[6000].dec <= 0.5, PERF.bench);
    // Leistung bei 720p im laufenden Anruf (Wartezeit inkl. Kodierer/Dekodierer auf derselben Maschine)
    await p.evaluate(async () => { await P.A.stats(true); await P.B.stats(true); });
    const o0 = await p.evaluate(() => outb(P.pa));
    await p.waitForTimeout(8000);
    const pf = await p.evaluate(async () => ({ a: await P.A.stats(), b: await P.B.stats(), o: await outb(P.pa), i: await inb(P.pb) }));
    const sum = (bag, pre) => Object.keys(bag).filter((k) => k.startsWith(pre)).reduce((s, k) => ({ n: s.n + bag[k].n, ms: s.ms + bag[k].ms, bytes: s.bytes + bag[k].bytes,
                                                                                                     max: Math.max(s.max, bag[k].max) }), { n: 0, ms: 0, bytes: 0, max: 0 });
    const ev = sum(pf.a.enc, 'video'), dv = sum(pf.b.dec, 'video'), ea = sum(pf.a.enc, 'audio'), da = sum(pf.b.dec, 'audio');
    PERF.video_px = pf.i.w + 'x' + pf.i.h;
    PERF.video_rahmen = ev.n; PERF.video_bytes_je_rahmen = Math.round(ev.bytes / Math.max(1, ev.n));
    PERF.video_kbit = Math.round((pf.o.bytes - o0.bytes) * 8 / 8000);
    PERF.enc_video_ms = +(ev.ms / Math.max(1, ev.n)).toFixed(4); PERF.dec_video_ms = +(dv.ms / Math.max(1, dv.n)).toFixed(4);
    PERF.enc_video_max_ms = +ev.max.toFixed(3); PERF.dec_video_max_ms = +dv.max.toFixed(3);
    PERF.enc_audio_ms = +(ea.ms / Math.max(1, ea.n)).toFixed(4); PERF.dec_audio_ms = +(da.ms / Math.max(1, da.n)).toFixed(4);
    ok(`Leistung 720p: ${PERF.enc_video_ms} ms verschluesseln, ${PERF.dec_video_ms} ms entschluesseln je Video-Rahmen ` +
       `(${PERF.video_bytes_je_rahmen} B, ${PERF.video_kbit} kbit/s; Grenze 0,5 ms)`,
       ev.n > 100 && dv.n > 100 && PERF.enc_video_ms <= 0.5 && PERF.dec_video_ms <= 0.5 && pf.i.w === 1280, pf.b.drop);
    await p.evaluate(() => { clearInterval(T.iv); P.pa.close(); P.pb.close(); P.A.close(); P.B.close(); });

    // schlimmster Fall: ganzes Bild Rauschen (sehr grosse Rahmen) - nur Messung
    await p.evaluate(async (m) => { window.N = await window.mkPair(m, true, true); const k = await N.A.own('start'); await N.B.peer('devA', k.n, k.key, k.s); }, mode);
    await p.waitForTimeout(4000);
    await p.evaluate(async () => { await N.A.stats(true); await N.B.stats(true); });
    await p.waitForTimeout(6000);
    const nz = await p.evaluate(async () => ({ a: await N.A.stats(), b: await N.B.stats() }));
    const nev = sum(nz.a.enc, 'video'), ndv = sum(nz.b.dec, 'video');
    PERF.rauschen_bytes_je_rahmen = Math.round(nev.bytes / Math.max(1, nev.n)); PERF.rauschen_rahmen = nev.n;
    PERF.rauschen_enc_ms = +(nev.ms / Math.max(1, nev.n)).toFixed(4); PERF.rauschen_dec_ms = +(ndv.ms / Math.max(1, ndv.n)).toFixed(4);
    ok(`schlimmster Fall Rauschen: ${PERF.rauschen_enc_ms}/${PERF.rauschen_dec_ms} ms je Rahmen (${PERF.rauschen_bytes_je_rahmen} B)`,
       nev.n > 10 && ndv.n > 10 && !nz.b.drop.auth, nz.b.drop);
    await p.evaluate(() => { clearInterval(T.iv); N.pa.close(); N.pb.close(); N.A.close(); N.B.close(); });

    // Rueckfall createEncodedStreams (aeltere Chromium ohne RTCRtpScriptTransform)
    const hasStreams = await p.evaluate(() => typeof RTCRtpSender.prototype.createEncodedStreams === 'function');
    if (hasStreams) {
      await p.evaluate(async () => {
        window.Q = await window.mkPair('streams', true);
        const k = await Q.A.own('start'); await Q.B.peer('devA', k.n, k.key, k.s);
      });
      const q = await until(async () => { const i = await p.evaluate(() => inb(Q.pb)); return i.fd > 20 && i.as > 0 && i; }, 15000);
      const qs = await p.evaluate(async () => ({ a: await Q.A.stats(), b: await Q.B.stats() }));
      ok('Rueckfall createEncodedStreams: Bild und Ton verschluesselt', q && qs.a.enc['video:delta'] && qs.b.dec['video:delta'] && qs.b.dec.audio && !qs.b.drop.auth,
         { q, qs, i: await p.evaluate(() => inb(Q.pb)), o: await p.evaluate(() => outb(Q.pa)) });
      await p.evaluate(() => { clearInterval(T.iv); Q.pa.close(); Q.pb.close(); Q.A.close(); Q.B.close(); });
    } else console.log('  (createEncodedStreams nicht vorhanden - Rueckfall nicht geprueft)');
  } catch (e) { console.error(e); res.push(false); }
  await browser.close();
  console.log('PERF ' + JSON.stringify(PERF));
  console.log(`\nBROWSER-HORN-E2E ${res.filter(Boolean).length}/${res.length}`, res.every(Boolean) ? '- ALLES GRUEN' : '- FEHLER');
  process.exit(res.every(Boolean) ? 0 : 1);
})();
