// Saga 2.5 - Messung: Reaktionen pruefen und oeffnen (X.openReactions aus static/chat-crypto.js, im Browser im Worker
// static/chat-worker.js op 'rx') - genau dem Code des Browsers: Signaturen ueber WebCrypto Ed25519 (openReactionsAsync),
// ohne WebCrypto-Ed25519 mit libsodium.js (openReactions, nur Angabe). Konzept-Grenze: <= 20 ms je typischer
// Seite (50 Nachrichten, je bis 5 Reaktionen), schlimmster Fall 50 x 50 = 2.500 Reaktionen <= 250 ms.
//   node tools/perf/x3_worker.mjs   -> Zeilen "PERFX3 ..." und Exit 1 bei Ueberschreitung
import { createRequire } from 'module';
import Module from 'module';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const require = createRequire(import.meta.url);
const SUMO = join(ROOT, 'static/vendor/libsodium/libsodium-sumo.js');
const orig = Module._resolveFilename;
Module._resolveFilename = function (req, ...a) { return req === 'libsodium-sumo' ? SUMO : orig.call(this, req, ...a); };
const sodium = require(join(ROOT, 'static/vendor/libsodium/libsodium-wrappers.js'));
await sodium.ready;
const X = require(join(ROOT, 'static/chat-crypto.js'))(sodium);

const ref = 'r'.repeat(43), people = [];
for (let i = 0; i < 50; i++) { const kp = sodium.crypto_sign_keypair(); people.push({ h: '~p' + i + '@saga.test', dev: 'd' + i, sk: kp.privateKey, pk: X.b64(kp.publicKey) }); }
const pks = {}; people.forEach(p => { pks[p.h + '|' + p.dev] = p.pk; });
const EM = ['👍', '❤️', '😂', '😮', '😢', '🙏'];
function page(nMsg, nRx) {
  const jobs = [];
  for (let m = 0; m < nMsg; m++) {
    const K = sodium.randombytes_buf(32), mid = sodium.to_hex(sodium.randombytes_buf(16)), items = [];
    for (let r = 0; r < nRx; r++) {
      const p = people[r];
      items.push(X.encryptReaction({ K, chat_ref: ref, msg_id: mid, handle: p.h, device: p.dev, sign_sk: p.sk, emoji: EM[r % 6], ts: 1700000000000 + r }));
    }
    jobs.push({ K, ref, mid, items });
  }
  return jobs;
}
// kalt (neue Seite, nichts im Zwischenspeicher): je Lauf frische Reaktionen; warm: dieselbe Seite noch einmal
async function time(mk, fn, runs = 5) {
  const cold = [], warm = [];
  for (let i = 0; i < runs; i++) {
    const jobs = mk(), n = jobs.reduce((a, j) => a + j.items.length, 0);
    let t0 = performance.now(), r = await fn(jobs, pks); cold.push(performance.now() - t0);
    if (Object.values(r).reduce((a, l) => a + l.length, 0) !== n) throw new Error('Reaktion nicht geoeffnet');
    t0 = performance.now(); await fn(jobs, pks); warm.push(performance.now() - t0);
  }
  const med = xs => Math.round(xs.sort((a, b) => a - b)[runs >> 1] * 10) / 10;
  return { cold: med(cold), warm: med(warm) };
}
const cases = [['typische Seite: 50 Nachrichten, 20 mit je 3 Reaktionen', () => page(20, 3), 20],
               ['Seite: 50 Nachrichten mit je 5 Reaktionen', () => page(50, 5), 0],
               ['schlimmster Fall: 50 x 50 Reaktionen (Browser: zwei Worker, tools/perf/x3_browser.js)', () => page(50, 50), 0]];
let bad = 0;
for (const [label, fn, gate] of [['WebCrypto Ed25519', X.openReactionsAsync, true], ['libsodium.js (Rueckfall)', X.openReactions, false]]) {
  for (const [name, mk, lim] of cases) {
    const t = await time(mk, fn);
    console.log(`PERFX3 Worker ${label}, ${name}: kalt ${t.cold} ms, warm ${t.warm} ms` + (lim ? ` (Grenze ${lim} ms${gate ? '' : ', nur Angabe'})` : ''));
    if (gate && lim && t.cold > lim) bad++;
  }
}
process.exit(bad ? 1 : 0);
