// Gegenprobe der Testvektoren fuer Gelesen-Bestaetigungen (tests/vectors/receipts.json, erzeugt mit PyNaCl) mit libsodium.js
// aus static/vendor/libsodium und static/chat-crypto.js - genau dem Code, den der Browser benutzt.   Aufruf: node tools/receipt_vectors.mjs
import { createRequire } from 'module';
import Module from 'module';
import { readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const SUMO = join(ROOT, 'static/vendor/libsodium/libsodium-sumo.js');
const orig = Module._resolveFilename;
Module._resolveFilename = function (req, ...a) { return req === 'libsodium-sumo' ? SUMO : orig.call(this, req, ...a); };
const sodium = require(join(ROOT, 'static/vendor/libsodium/libsodium-wrappers.js'));
await sodium.ready;
const C = require(join(ROOT, 'static/chat-crypto.js'))(sodium);
const v = JSON.parse(readFileSync(join(ROOT, 'tests/vectors/receipts.json'), 'utf8'));
const res = [];
const ok = (n, c) => { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n); };
const hx = (h) => sodium.from_hex(h);

ok('format', v.format === 'saga-receipt/v1');
const sk = sodium.crypto_sign_seed_keypair(hx(v.device.sign_seed_hex)), bk = sodium.crypto_box_seed_keypair(hx(v.viewer.backup_seed_hex));
ok('schluessel', C.b64(sk.publicKey) === v.device.sign_pk && C.b64(bk.publicKey) === v.viewer.backup_pk);
for (const c of v.cases) {
  ok(c.name + ' lp', sodium.to_hex(C.receiptMsg(c.chat, c.reader, v.device.id, c.body, c.v2)) === c.msg_hex);
  const r = C.openReceipt(c.sealed, bk.publicKey, bk.privateKey);
  ok(c.name + ' oeffnen + pruefen', r.dev === v.device.id && r.body === c.body && C.verifyReceipt(c.chat, c.reader, r, sk.publicKey, c.v2));
  // selbst versiegeln (zufaellig) -> Signatur fest wie in der Datei
  const s = C.sealReceipt({ chat: c.chat, reader: c.reader, device: v.device.id, sign_sk: sk.privateKey, body: JSON.parse(c.body), v2: c.v2, to_pk: bk.publicKey });
  const r2 = C.openReceipt(s, bk.publicKey, bk.privateKey);
  ok(c.name + ' versiegeln (Signatur wie PyNaCl)', r2.body === c.body && C.b64(r2.sig) === c.sig);
}
for (const r of v.reject) {
  ok('ablehnen: ' + r.why, !C.verifyReceipt(r.chat, r.reader, { dev: v.device.id, body: r.body, sig: C.unb64(r.sig, 64) }, sk.publicKey, r.v2));
}
const all = res.every(Boolean);
console.log(`\n${res.filter(Boolean).length}/${res.length} OK – ` + (all ? 'ALLES GRUEN' : 'FEHLER'));
process.exit(all ? 0 : 1);
