// Saga 2.5 - Messung im Browser: Reaktionen pruefen und oeffnen im echten Chat-Worker (static/chat-worker.js, op 'rx';
// Signaturen ueber WebCrypto Ed25519, sonst libsodium.js). Ohne Server: Playwright liefert static/ unter https://bench.test aus.
// Konzept-Grenze: <= 20 ms je typischer Seite, schlimmster Fall 50 x 50 = 2.500 Reaktionen <= 250 ms (jeweils kalt; den
// schlimmsten Fall verteilt static/chat.js auf zwei Worker, die neuesten Nachrichten kommen zuerst).
//   PLAYWRIGHT=/pfad/zu/playwright node tools/perf/x3_browser.js   -> Zeilen "PERFX3 ..." , Exit 1 bei Ueberschreitung
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path');
const ROOT = path.join(__dirname, '..', '..');
const TYPES = { '.js': 'application/javascript', '.html': 'text/html', '.wasm': 'application/wasm' };

(async () => {
  const browser = await chromium.launch({ channel: process.env.PW_CHANNEL || 'chromium', headless: true, args: ['--disable-background-networking', '--no-proxy-server'] });
  const page = await browser.newPage();
  await page.route('https://bench.test/**', r => {
    const u = new URL(r.request().url());
    if (u.pathname === '/') return r.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><script src="/static/vendor/libsodium/libsodium-sumo.js"></script><script src="/static/vendor/libsodium/libsodium-wrappers.js"></script><script src="/static/chat-crypto.js"></script>' });
    const f = path.join(ROOT, path.normalize(u.pathname));
    if (!f.startsWith(path.join(ROOT, 'static')) || !fs.existsSync(f)) return r.fulfill({ status: 404, body: '' });
    return r.fulfill({ status: 200, contentType: TYPES[path.extname(f)] || 'application/octet-stream', body: fs.readFileSync(f) });
  });
  await page.goto('https://bench.test/');
  const out = await page.evaluate(async () => {
    await window.sodium.ready;
    const S = window.sodium, X = window.SagaChatCrypto(S), w = new Worker('/static/chat-worker.js?v=bench');
    let n = 0; const cb = {};
    w.onmessage = e => { const c = cb[e.data.n]; delete cb[e.data.n]; c(e.data); };
    const call = d => new Promise(res => { d.n = ++n; cb[d.n] = res; w.postMessage(d); });
    const ref = 'r'.repeat(43), people = [], pks = {}, EM = ['👍', '❤️', '😂', '😮', '😢', '🙏'];
    for (let i = 0; i < 50; i++) { const kp = S.crypto_sign_keypair(); people.push({ h: '~p' + i + '@saga.test', dev: 'd' + i, sk: kp.privateKey }); pks['~p' + i + '@saga.test|d' + i] = X.b64(kp.publicKey); }
    const mk = (nMsg, nRx) => Array.from({ length: nMsg }, () => {
      const K = S.randombytes_buf(32), mid = S.to_hex(S.randombytes_buf(16));
      return { K, ref, mid, items: people.slice(0, nRx).map((p, r) => X.encryptReaction({ K, chat_ref: ref, msg_id: mid, handle: p.h, device: p.dev, sign_sk: p.sk, emoji: EM[r % 6], ts: 1700000000000 + r })) };
    });
    await call({ op: 'rx', jobs: mk(2, 2), pks });          // Worker laden (libsodium) - nicht mitgemessen
    const res = {};
    for (const [name, a, b] of [['typische Seite (20 von 50 Nachrichten mit je 3)', 20, 3], ['50 Nachrichten mit je 5', 50, 5],
                                ['sichtbarer Teil zuerst (15 Nachrichten mit je 5)', 15, 5], ['schlimmster Fall 50 x 50', 50, 50]]) {
      const cold = [], warm = [];
      for (let i = 0; i < 5; i++) {
        const jobs = mk(a, b);
        let t0 = performance.now(); const r = await call({ op: 'rx', jobs, pks }); cold.push(performance.now() - t0);
        if (!r.ok || Object.values(r.rx).reduce((s, l) => s + l.length, 0) !== a * b) return { err: 'nicht geoeffnet: ' + name };
        t0 = performance.now(); await call({ op: 'rx', jobs, pks }); warm.push(performance.now() - t0);
      }
      const med = xs => Math.round(xs.sort((x, y) => x - y)[2] * 10) / 10;
      res[name] = { cold: med(cold), warm: med(warm) };
    }
    {                                                   // schlimmster Fall auf zwei Worker verteilt (wie static/chat.js ab 500 Reaktionen)
      const w2 = new Worker('/static/chat-worker.js?v=bench'); const cb2 = {}; let n2 = 0;
      w2.onmessage = e => { const c = cb2[e.data.n]; delete cb2[e.data.n]; c(e.data); };
      const call2 = d => new Promise(res => { d.n = ++n2; cb2[d.n] = res; w2.postMessage(d); });
      await call2({ op: 'rx', jobs: mk(2, 2), pks });
      const cold = [];
      for (let i = 0; i < 5; i++) {
        const jobs = mk(50, 50), t0 = performance.now();
        await Promise.all([call({ op: 'rx', jobs: jobs.slice(0, 25), pks }), call2({ op: 'rx', jobs: jobs.slice(25), pks })]);
        cold.push(performance.now() - t0);
      }
      res['schlimmster Fall 50 x 50, zwei Worker'] = { cold: Math.round(cold.sort((x, y) => x - y)[2] * 10) / 10, warm: 0 };
    }
    const wc = await crypto.subtle.importKey('raw', S.crypto_sign_keypair().publicKey, { name: 'Ed25519' }, false, ['verify']).then(() => true, () => false);
    return { res, wc, ua: navigator.userAgent.match(/Chrome\/[\d.]+/)?.[0] };
  });
  await browser.close();
  if (out.err) { console.log('FAIL ' + out.err); process.exit(1); }
  const LIM = { 'typische Seite (20 von 50 Nachrichten mit je 3)': 20, 'sichtbarer Teil zuerst (15 Nachrichten mit je 5)': 20, 'schlimmster Fall 50 x 50, zwei Worker': 250 };
  let bad = 0;
  for (const [k, v] of Object.entries(out.res)) {
    const lim = LIM[k], over = lim && v.cold > lim; bad += over ? 1 : 0;
    console.log(`PERFX3 Browser-Worker (${out.ua}, WebCrypto Ed25519 ${out.wc ? 'ja' : 'nein'}) ${k}: kalt ${v.cold} ms, warm ${v.warm} ms` + (lim ? ` (Grenze ${lim} ms${over ? ' UEBERSCHRITTEN' : ''})` : ''));
  }
  process.exit(bad ? 1 : 0);
})();
