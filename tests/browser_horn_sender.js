// Bughunt 3: Sender des Web-Knotenpfads (static/calls.js hornLeg) im headless Chromium nachgestellt - ohne Knoten, zwei
// PeerConnections im selben Fenster (Loopback), mit dem echten Frame-E2E-Worker (static/frame-e2e.js):
//   SAGA_BASE=http://host:port [PLAYWRIGHT=playwright-core] node tests/browser_horn_sender.js
// Geprueft (Gegenstueck zu den iOS-Funden, libwebrtc M150):
//  1. Simulcast wie hornLeg (UDP: l 1/4 20 fps L1T2, m 1/2 30 fps L1T3, h voll 30 fps L1T3; TCP: l + m): jede Schicht
//     kodiert Bilder (framesEncoded > 0), die oberste in voller Hoehe - mit und ohne E2E-Transform.
//  2. Bildschirm-Sender ohne Spur beim Aufbau, Transform vorher angehaengt, Spur spaeter per replaceTrack (wie hornScreen):
//     Rahmen gehen raus und kommen beim Empfaenger entschluesselt an (framesDecoded > 0).
//  3. Stehender Bildschirm (Aufnahme des eigenen, unveraenderten Tabs per getDisplayMedia) und Schluessel beim Empfaenger
//     erst spaeter (fkey kommt nach den ersten Rahmen): danach Schluesselbild und dekodierte Bilder. Canvas ohne Bildwechsel
//     nur als Information (liefert keine Rahmen, also auch kein Schluesselbild auf PLI).
// WebRTC braucht eine Netz-Schnittstelle ausser lo.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE) { console.error('SAGA_BASE setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (info === undefined ? '' : ' -> ' + JSON.stringify(info))); }

// Laeuft im Fenster: zwei PeerConnections, Antwort mit rid/simulcast-Zeilen ergaenzt (der Empfaenger nimmt nur eine Schicht,
// die Sender-Statistik zaehlt alle)
const PAGE = function () {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function munge(answer, rids) {
    if (!rids) return answer;
    return answer.replace(/(m=video[^\r\n]*\r\n)/, '$1').replace(/a=mid:0\r\n/, 'a=mid:0\r\n' + rids.map((r) => 'a=rid:' + r + ' recv\r\n').join('') +
      'a=simulcast:recv ' + rids.join(';') + '\r\n');
  }
  function strip(offer) { return offer.split('\r\n').filter((l) => !/^a=(rid|simulcast):/.test(l)).join('\r\n'); }
  async function connect(pc1, pc2, rids) {
    pc1.onicecandidate = (e) => e.candidate && pc2.addIceCandidate(e.candidate).catch(() => {});
    pc2.onicecandidate = (e) => e.candidate && pc1.addIceCandidate(e.candidate).catch(() => {});
    const o = await pc1.createOffer();
    await pc1.setLocalDescription(o);
    await pc2.setRemoteDescription({ type: 'offer', sdp: strip(o.sdp) });
    const a = await pc2.createAnswer();
    await pc2.setLocalDescription(a);
    await pc1.setRemoteDescription({ type: 'answer', sdp: munge(a.sdp, rids) });
  }
  async function outStats(pc) {
    const out = {};
    (await pc.getStats()).forEach((s) => {
      if (s.type === 'outbound-rtp' && s.kind === 'video') out[s.rid || 'x'] = { enc: s.framesEncoded || 0, h: s.frameHeight || 0, bytes: s.bytesSent || 0, lim: s.qualityLimitationReason, mode: s.scalabilityMode };
    });
    return out;
  }
  async function inStats(pc) {
    let r = { dec: 0, key: 0, pli: 0 };
    (await pc.getStats()).forEach((s) => { if (s.type === 'inbound-rtp' && s.kind === 'video') r = { dec: s.framesDecoded || 0, key: s.keyFramesDecoded || 0, pli: s.pliCount || 0, recv: s.framesReceived || 0 }; });
    return r;
  }
  window.T = {
    // 1. Simulcast wie hornLeg
    async simulcast(path, e2e, plain) {
      const cam = (await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720, frameRate: 30 } })).getVideoTracks()[0];
      const X = e2e ? window.SagaFrameE2E.create({ url: '/static/frame-e2e.js' }) : null;
      if (X) await X.own('start');
      const pc1 = new RTCPeerConnection({ encodedInsertableStreams: X && X.mode === 'streams' }), pc2 = new RTCPeerConnection();
      let enc = path === 'udp' ? [
        { rid: 'l', scaleResolutionDownBy: 4, maxBitrate: 150000, maxFramerate: 20, scalabilityMode: 'L1T2' },
        { rid: 'm', scaleResolutionDownBy: 2, maxBitrate: 500000, maxFramerate: 30, scalabilityMode: 'L1T3' },
        { rid: 'h', scaleResolutionDownBy: 1, maxBitrate: 1500000, maxFramerate: 30, scalabilityMode: 'L1T3' }] : [
        { rid: 'l', scaleResolutionDownBy: 4, maxBitrate: 150000, maxFramerate: 20, scalabilityMode: 'L1T2' },
        { rid: 'm', scaleResolutionDownBy: 1, maxBitrate: 700000, maxFramerate: 30, scalabilityMode: 'L1T3' }];
      if (plain) enc = enc.map((x) => ({ rid: x.rid, scaleResolutionDownBy: x.scaleResolutionDownBy, maxBitrate: x.maxBitrate }));
      const tr = pc1.addTransceiver(cam, { direction: 'sendonly', streams: [new MediaStream()], sendEncodings: enc });
      if (X) X.sender(tr.sender, 'video');
      pc2.ontrack = () => {};
      await connect(pc1, pc2, enc.map((x) => x.rid));
      await sleep(7000);
      const st = await outStats(pc1);
      const params = tr.sender.getParameters().encodings.map((x) => ({ rid: x.rid, fps: x.maxFramerate, sm: x.scalabilityMode }));
      pc1.close(); pc2.close(); cam.stop(); if (X) X.close();
      return { st, params };
    },
    // 2./3. Bildschirm-Sender ohne Spur beim Aufbau; static: Canvas, das sich nach dem ersten Bild nicht mehr aendert;
    // keyLate: Empfaenger bekommt den Schluessel erst nach 3 s
    async screen(staticPic, keyLate) {
      const XS = window.SagaFrameE2E.create({ url: '/static/frame-e2e.js' }), XR = window.SagaFrameE2E.create({ url: '/static/frame-e2e.js' });
      const k = await XS.own('start');
      const streams = XS.mode === 'streams';
      const pc1 = new RTCPeerConnection({ encodedInsertableStreams: streams }), pc2 = new RTCPeerConnection({ encodedInsertableStreams: streams });
      const tr = pc1.addTransceiver('video', { direction: 'sendonly', sendEncodings: [{ maxBitrate: 2500000, maxFramerate: 15 }] });
      XS.sender(tr.sender, 'video');
      pc2.ontrack = (e) => { XR.receiver(e.receiver, 'video', 'sender-1', '', 1); };
      await connect(pc1, pc2, null);
      if (!keyLate) await XR.peer('sender-1', k.n, k.key, k.s);
      await sleep(1500);
      let track;
      if (staticPic === 'tab') {
        document.body.innerHTML = '<h1 style="font:80px sans-serif">Folie 1</h1>';
        track = (await navigator.mediaDevices.getDisplayMedia({ video: true, preferCurrentTab: true })).getVideoTracks()[0];
        try { track.contentHint = 'detail'; } catch (e) { /* alt */ }
      } else if (staticPic) {
        const cv = document.createElement('canvas'); cv.width = 1280; cv.height = 720;
        const g = cv.getContext('2d'); g.fillStyle = '#246'; g.fillRect(0, 0, 1280, 720); g.fillStyle = '#fff'; g.font = '80px sans-serif'; g.fillText('Folie 1', 100, 300);
        track = cv.captureStream().getVideoTracks()[0];
        try { track.contentHint = 'detail'; } catch (e) { /* alt */ }
      } else {
        track = (await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 } })).getVideoTracks()[0];
      }
      await tr.sender.replaceTrack(track);
      await sleep(3000);
      const mid = { out: await outStats(pc1), inn: await inStats(pc2) };
      if (keyLate) await XR.peer('sender-1', k.n, k.key, k.s);
      await sleep(4000);
      const fin = { out: await outStats(pc1), inn: await inStats(pc2), e2e: await XR.stats() };
      pc1.close(); pc2.close(); track.stop(); XS.close(); XR.close();
      return { mid, fin, drops: fin.e2e && fin.e2e.drop };
    },
  };
};

(async () => {
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || undefined, args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--auto-accept-this-tab-capture', '--auto-select-tab-capture-source-by-title=Saga'] });
  const ctx = await browser.newContext({ bypassCSP: true });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error') console.log('  [browser]', m.text().slice(0, 200)); });
  await page.goto(BASE + '/login');
  await page.addScriptTag({ url: BASE + '/static/frame-e2e.js' });
  await page.evaluate(PAGE);
  const mode = await page.evaluate(() => window.SagaFrameE2E.supported());
  console.log('Chromium', browser.version(), 'Frame-E2E:', mode);

  for (const path of ['udp', 'tcp']) {
    for (const e2e of [false, true]) {
      const r = await page.evaluate(([p, x]) => window.T.simulcast(p, x, false), [path, e2e]);
      const top = path === 'udp' ? 'h' : 'm';
      const all = Object.keys(r.st).length === (path === 'udp' ? 3 : 2) && Object.values(r.st).every((s) => s.enc > 30);
      ok(`Simulcast ${path}${e2e ? ' mit E2E' : ''}: alle Schichten kodieren, oberste 720p`, all && r.st[top] && r.st[top].h === 720, r.st);
    }
  }
  const pl = await page.evaluate(() => window.T.simulcast('udp', true, true));
  console.log('  Gegenprobe ohne maxFramerate/scalabilityMode:', JSON.stringify(pl.st));

  let r = await page.evaluate(() => window.T.screen(false, false));
  ok('Bildschirm: Sender ohne Spur beim Aufbau, Spur spaeter -> Empfaenger dekodiert', r.fin.inn.dec > 10, r.fin);
  r = await page.evaluate(() => window.T.screen(true, false));
  ok('Stehender Bildschirm (Canvas): Empfaenger dekodiert mindestens ein Bild', r.fin.inn.dec >= 1, r.fin);
  r = await page.evaluate(() => window.T.screen(true, true));
  // Nur zur Information: eine Canvas-Quelle liefert ohne Bildwechsel keine neuen Rahmen - auf PLI kommt dann kein Schluesselbild
  // (Chromium 153: 1 kodiertes Bild, > 20 PLI). Saga teilt den Bildschirm ueber getDisplayMedia (unten), dort kommen Rahmen.
  console.log('  Info Canvas ohne Bildwechsel, Schluessel spaeter:', JSON.stringify({ enc: (r.fin.out.x || {}).enc, inn: r.fin.inn }));
  try {
    r = await page.evaluate(() => window.T.screen('tab', false));
    console.log('  Tab-Aufnahme (stehend), Schluessel sofort:', JSON.stringify({ out: r.fin.out, inn: r.fin.inn }));
    r = await page.evaluate(() => window.T.screen('tab', true));
    ok('Stehender Tab (getDisplayMedia), Schluessel kommt spaeter: danach dekodiert', r.fin.inn.dec >= 1, { mid: r.mid, fin: r.fin.inn, drops: r.drops });
  } catch (e) { console.log('  Tab-Aufnahme nicht moeglich:', String(e).slice(0, 200)); }
  r = await page.evaluate(() => window.T.screen(false, true));
  ok('Bewegter Bildschirm, Schluessel kommt spaeter: danach dekodiert', r.fin.inn.dec > 10, { mid: r.mid.inn, fin: r.fin.inn });

  await browser.close();
  console.log(`\nBROWSER-HORN-SENDER ${res.filter(Boolean).length}/${res.length}`, res.every(Boolean) ? '- ALLES GRUEN' : '- FEHLER');
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
