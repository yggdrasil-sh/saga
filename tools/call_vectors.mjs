// Gegenprobe der Anruf-Testvektoren (tests/call_vectors.json, erzeugt mit PyNaCl) mit libsodium.js aus
// static/vendor/libsodium und static/chat-crypto.js - genau dem Code, den der Browser benutzt.   Aufruf: node tools/call_vectors.mjs
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
const v = JSON.parse(readFileSync(join(ROOT, 'tests/call_vectors.json'), 'utf8'));
const res = [];
const ok = (n, c) => { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n); };
const hx = (h) => sodium.from_hex(h), b = C.b64, u = C.unb64;
const { caller: ca, callee: ce, call_id: cid, offer: o } = v;
const ea = sodium.crypto_box_seed_keypair(hx(ca.enc_seed_hex)), sa = sodium.crypto_sign_seed_keypair(hx(ca.sign_seed_hex));
const eb = sodium.crypto_box_seed_keypair(hx(ce.enc_seed_hex)), sb = sodium.crypto_sign_seed_keypair(hx(ce.sign_seed_hex));
ok('call device keys', b(sa.publicKey) === ca.sign_pk && b(eb.publicKey) === ce.enc_pk && b(ea.publicKey) === ca.enc_pk && b(sb.publicKey) === ce.sign_pk);
const meta = { call_id: cid, from_uid: ca.uid, from_device: ca.device, to_uid: ce.uid, to_device: ce.device };
ok('call signal_msg', sodium.to_hex(C.signalMsg(cid, ca.uid, ca.device, ce.uid, ce.device, o.body)) === o.signal_msg_hex);
ok('call sig (Ed25519, deterministisch)', b(C.signalSig(Object.assign({ body: o.body, sign_sk: sa.privateKey }, meta))) === o.sig);
ok('call inner', C.signalInner(o.body, u(o.sig, 64)) === o.inner);
ok('call sdp fingerprints', JSON.stringify(C.sdpFingerprints(JSON.parse(o.body).sdp)) === JSON.stringify(o.fingerprints));
let s = C.openSignal(o.sealed, eb.publicKey, eb.privateKey);
ok('call open offer', s.body === o.body && b(s.sig) === o.sig);
ok('call verify offer', C.verifySignal(Object.assign({ body: s.body }, meta), s.sig, sa.publicKey));
ok('call verify fails for other call', !C.verifySignal(Object.assign({ body: s.body }, meta, { call_id: '0'.repeat(32) }), s.sig, sa.publicKey));
s = C.openSignal(v.ice.sealed, eb.publicKey, eb.privateKey);
ok('call open+verify ice', s.body === v.ice.body && b(s.sig) === v.ice.sig && C.verifySignal(Object.assign({ body: s.body }, meta), s.sig, sa.publicKey));
s = C.openSignal(v.forged.sealed, eb.publicKey, eb.privateKey);
ok('call forged (other target device) rejected', !C.verifySignal(Object.assign({ body: s.body }, meta), s.sig, sa.publicKey));
// Gegenrichtung: libsodium.js versiegelt, oeffnen + pruefen mit denselben Funktionen
const sealed = C.sealSignal(Object.assign({ body: v.ice.body, sign_sk: sa.privateKey, to_pk: eb.publicKey }, meta));
s = C.openSignal(sealed, eb.publicKey, eb.privateKey);
ok('call seal roundtrip', s.body === v.ice.body && C.verifySignal(Object.assign({ body: s.body }, meta), s.sig, sa.publicKey));
const all = res.every(Boolean);
console.log(`VEKTOREN (libsodium.js ${sodium.SODIUM_VERSION_STRING || ''}) ${res.filter(Boolean).length}/${res.length}`, all ? '- ALLES GRUEN' : '- FEHLER');
process.exit(all ? 0 : 1);
