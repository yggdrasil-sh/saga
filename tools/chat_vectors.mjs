// Gegenprobe der Chat-Testvektoren (tests/chat_vectors.json, erzeugt mit PyNaCl) mit libsodium.js aus static/vendor/libsodium
// und static/chat-crypto.js - genau dem Code, den der Browser benutzt.   Aufruf: node tools/chat_vectors.mjs
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
const v = JSON.parse(readFileSync(join(ROOT, 'tests/chat_vectors.json'), 'utf8'));
const res = [];
const ok = (n, c) => { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n); };
const hx = (h) => sodium.from_hex(h), b = C.b64, u = C.unb64, eq = (a, c) => sodium.to_hex(a) === sodium.to_hex(c);
const { identity: i, device: d, recovery: r, message: m, file: f, link: lk, safety: s } = v;

ok('lp', sodium.to_hex(C.lp(...v.lp.fields)) === v.lp.hex);
ok('base32', C.base32(hx(v.base32.bytes_hex)) === v.base32.text);
const ik = sodium.crypto_sign_seed_keypair(hx(i.ident_seed_hex)), bk = sodium.crypto_box_seed_keypair(hx(i.backup_seed_hex));
ok('identity keys', b(ik.publicKey) === i.ident_pk && b(ik.privateKey) === i.ident_sk && b(bk.publicKey) === i.backup_pk && b(bk.privateKey) === i.backup_sk);
ok('backup_sig', b(C.backupSig(i.uid, ik.privateKey, bk.publicKey)) === i.backup_sig && C.verifyBackup(i.uid, ik.publicKey, i.backup_pk, i.backup_sig));
const ek = sodium.crypto_box_seed_keypair(hx(d.enc_seed_hex)), sk = sodium.crypto_sign_seed_keypair(hx(d.sign_seed_hex));
ok('device keys', b(ek.publicKey) === d.enc_pk && b(sk.publicKey) === d.sign_pk && b(sk.privateKey) === d.sign_sk);
const dev = { id: d.id, enc_pk: ek.publicKey, enc_sk: ek.privateKey, sign_pk: sk.publicKey, sign_sk: sk.privateKey };
ok('device cert', sodium.to_hex(C.deviceMsg(i.uid, d.id, dev.enc_pk, dev.sign_pk)) === d.msg_hex && b(C.deviceSig(i.uid, ik.privateKey, dev)) === d.sig
  && C.verifyDevice(i.uid, ik.publicKey, { id: d.id, enc_pub: d.enc_pk, sign_pub: d.sign_pk, sig: d.sig }));
ok('recovery normalize', C.normRecovery(r.key) === r.normalized && C.normRecovery(r.input_variant) === r.normalized && C.formatRecovery(r.normalized) === r.key);
ok('recovery kdf (argon2id13)', sodium.to_hex(C.recoveryKdf(r.key, u(r.salt), r.ops, r.mem)) === r.kdf_hex);
ok('bundle seal', C.sealBundle(i.uid, r.key, null, null, { salt: u(r.salt), nonce: u(r.nonce), plain: r.plain }) === r.bundle);
const ob = C.openBundle(i.uid, r.input_variant, r.bundle);
ok('bundle open', eq(ob.ident_pk, ik.publicKey) && eq(ob.backup_pk, bk.publicKey) && C.bundlePlain(ob.ident_sk, ob.backup_sk) === r.plain);
const e = C.encryptMessage({ chat_id: m.chat_id, sender: m.sender, device: m.device, sign_sk: dev.sign_sk, plain: m.body_json, reply_to: m.reply_to,
  recipients: [], id: m.id, ts: m.ts, key: u(m.key), nonce: u(m.nonce) });
ok('message ad', sodium.to_hex(C.msgAd(m.chat_id, m.id, m.sender, m.device, m.ts, m.reply_to)) === m.ad_hex);
ok('message encrypt', e.body_ct === m.body_ct);
ok('message sig', e.sig === m.sig && sodium.to_hex(C.sigMsg(m.chat_id, m.id, m.sender, m.device, m.ts, m.reply_to, m.body_ct)) === m.sig_msg_hex);
const api = { id: m.id, ts: m.ts, sender: { username: m.sender }, sender_device: m.device, body_ct: m.body_ct, reply_to: m.reply_to, sig: m.sig };
ok('message verify', C.verifyMessage(m.chat_id, api, dev.sign_pk) && !C.verifyMessage(m.chat_id, Object.assign({}, api, { ts: m.ts + 1 }), dev.sign_pk));
const want = JSON.stringify(JSON.parse(m.body_json));
ok('message open (device)', JSON.stringify(C.decryptMessage(m.chat_id, api, C.openKey(m.sealed_device, dev.enc_pk, dev.enc_sk))) === want);
ok('message open (backup)', JSON.stringify(C.decryptMessage(m.chat_id, api, C.openKey(m.sealed_backup, bk.publicKey, bk.privateKey))) === want);
const fe = C.encryptFile(f.chat_id, u(f.plain_b64), { key: u(f.key), nonce: u(f.nonce) });
ok('file', b(fe.blob) === f.blob && b(C.decryptFile(f.chat_id, u(f.blob), f.key)) === f.plain_b64);
ok('link code', C.linkCode(lk.uid, lk.device_id, u(lk.enc_pk), u(lk.sign_pk)) === lk.code);
ok('link sealed bundle', C.text(C.sealOpen(u(lk.sealed_bundle), dev.enc_pk, dev.enc_sk)) === r.plain);
ok('safety number', C.safetyNumber(s.uid_a, u(s.ident_a), s.uid_b, u(s.ident_b)) === s.number && C.safetyNumber(s.uid_b, u(s.ident_b), s.uid_a, u(s.ident_a)) === s.number);
// Gegenrichtung: libsodium.js versiegelt/signiert, PyNaCl prueft (tests/chat.py liest diese Ausgabe nicht - nur Selbsttest hier)
const k2 = sodium.crypto_aead_xchacha20poly1305_ietf_keygen(), sealed = sodium.crypto_box_seal(k2, dev.enc_pk);
ok('seal roundtrip', eq(sodium.crypto_box_seal_open(sealed, dev.enc_pk, dev.enc_sk), k2));
const all = res.every(Boolean);
console.log(`VEKTOREN (libsodium.js ${sodium.SODIUM_VERSION_STRING || ''}) ${res.filter(Boolean).length}/${res.length}`, all ? '- ALLES GRUEN' : '- FEHLER');
process.exit(all ? 0 : 1);
