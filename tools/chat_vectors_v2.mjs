// Gegenprobe der Testvektoren saga-chat/v2 (tests/vectors/chat_v2.json, erzeugt mit PyNaCl) mit libsodium.js aus
// static/vendor/libsodium und static/chat-crypto.js - genau dem Code, den der Browser benutzt.   Aufruf: node tools/chat_vectors_v2.mjs
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
const v = JSON.parse(readFileSync(join(ROOT, 'tests/vectors/chat_v2.json'), 'utf8'));
const res = [];
const ok = (n, c) => { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n); };
const hx = (h) => sodium.from_hex(h), b = C.b64, u = C.unb64;
const { identity: i, handle_cert: hc, device: d, recipient: rc, file: f, safety: s } = v;

ok('format', v.format === 'saga-chat/v2');
ok('lp', sodium.to_hex(C.lp(...v.lp.fields)) === v.lp.hex);
const ik = sodium.crypto_sign_seed_keypair(hx(i.ident_seed_hex)), bk = sodium.crypto_box_seed_keypair(hx(i.backup_seed_hex));
ok('identity keys', b(ik.publicKey) === i.ident_pk && b(bk.publicKey) === i.backup_pk);
ok('handle cert', sodium.to_hex(C.handleMsg(hc.handle, ik.publicKey, bk.publicKey)) === hc.msg_hex
  && b(C.handleSig(hc.handle, ik.privateKey, ik.publicKey, bk.publicKey)) === hc.sig);
const ek = sodium.crypto_box_seed_keypair(hx(d.enc_seed_hex)), sk = sodium.crypto_sign_seed_keypair(hx(d.sign_seed_hex));
const dev = { id: d.id, enc_pk: ek.publicKey, sign_pk: sk.publicKey, sign_sk: sk.privateKey };
ok('device cert v2', sodium.to_hex(C.deviceMsgV2(i.handle, d.id, dev.enc_pk, dev.sign_pk)) === d.msg_hex && b(C.deviceSigV2(i.handle, ik.privateKey, dev)) === d.fed_sig);
const entry = { handle: i.handle, ident_pub: i.ident_pk, backup_pub: i.backup_pk, sig: hc.sig,
  devices: [{ id: d.id, enc_pub: d.enc_pk, sign_pub: d.sign_pk, fed_sig: d.fed_sig, revoked: false },
            { id: 'ffffffffffffffffffffffffffffffff', enc_pub: d.enc_pk, sign_pub: d.sign_pk, fed_sig: d.fed_sig, revoked: false }] };
const ce = C.checkKeysV2(i.handle, entry);
ok('checkKeysV2 (gueltig, falsches Geraet faellt weg)', ce && ce.devices.length === 1 && ce.devices[0].id === d.id);
ok('checkKeysV2 (anderer Handle) -> null', C.checkKeysV2(rc.identity.handle, Object.assign({}, entry, { handle: rc.identity.handle })) === null);
for (const [name, m] of Object.entries(v.messages)) {
  const e = C.encryptMessageV2({ chat_ref: m.chat_ref, sender_handle: m.sender_handle, device: m.sender_device, sign_sk: dev.sign_sk, plain: m.body_json,
    reply_to: m.reply_to, ai: m.ai, recipients: [], id: m.id, ts: m.ts, key: u(m.key), nonce: u(m.nonce) });
  ok(name + ' ad', sodium.to_hex(C.msgAdV2(m.chat_ref, m.id, m.sender_handle, m.sender_device, m.ts, m.reply_to, m.ai)) === m.ad_hex);
  ok(name + ' encrypt', e.body_ct === m.body_ct);
  ok(name + ' sig', e.sig === m.sig && sodium.to_hex(C.sigMsgV2(m.chat_ref, m.id, m.sender_handle, m.sender_device, m.ts, m.reply_to, m.ai, m.body_ct)) === m.sig_msg_hex);
  ok(name + ' verify', C.verifyMessageV2(m.chat_ref, m, dev.sign_pk));
  ok(name + ' open', JSON.stringify(C.decryptMessageV2(m.chat_ref, m, u(m.key))) === JSON.stringify(JSON.parse(m.body_json)));
}
const bkb = sodium.crypto_box_seed_keypair(hx(rc.identity.backup_seed_hex)), ekb = sodium.crypto_box_seed_keypair(hx(rc.device.enc_seed_hex));
const hostB = rc.identity.handle.slice(rc.identity.handle.indexOf('@') + 1), mm = v.messages[rc.message];
const want = JSON.stringify(JSON.parse(mm.body_json));
ok('open sealed (device)', JSON.stringify(C.decryptMessageV2(mm.chat_ref, mm, C.openKey(rc.sealed['dev:' + rc.device.id + '@' + hostB], ekb.publicKey, ekb.privateKey))) === want);
ok('open sealed (backup)', JSON.stringify(C.decryptMessageV2(mm.chat_ref, mm, C.openKey(rc.sealed['backup:' + rc.identity.handle], bkb.publicKey, bkb.privateKey))) === want);
const rv = C.recipientsV2([{ handle: rc.identity.handle, backup_pk: bkb.publicKey, devices: [{ id: rc.device.id, enc_pub: rc.device.enc_pk }] }]);
ok('recipient ids', JSON.stringify(rv.map((r) => r.id).sort()) === JSON.stringify(rc.ids.slice().sort()));
const fe = C.encryptFileV2(f.chat_ref, u(f.plain_b64), { key: u(f.key), nonce: u(f.nonce) });
ok('file', b(fe.blob) === f.blob && b(C.decryptFileV2(f.chat_ref, u(f.blob), f.key)) === f.plain_b64);
ok('safety number v2', C.safetyNumberV2(s.handle_a, u(s.ident_a), s.handle_b, u(s.ident_b)) === s.number
  && C.safetyNumberV2(s.handle_b, u(s.ident_b), s.handle_a, u(s.ident_a)) === s.number);
for (const r of v.reject) {
  let good;
  if (r.what === 'handle_cert') good = C.checkKeysV2(r.handle, Object.assign({}, entry, { handle: r.handle, sig: r.sig })) !== null;
  else if (r.what === 'device_cert') good = (C.checkKeysV2(i.handle, Object.assign({}, entry, { devices: [{ id: d.id, enc_pub: d.enc_pk, sign_pub: d.sign_pk, fed_sig: r.fed_sig }] })) || { devices: [] }).devices.length > 0
    && r.handle === i.handle;
  else {
    const m = Object.assign({}, v.messages[r.message]);
    ['sender_handle', 'ai', 'ts'].forEach((k) => { if (k in r) m[k] = r[k]; });
    good = C.verifyMessageV2(r.chat_ref || m.chat_ref, m, dev.sign_pk);
  }
  ok('reject: ' + r.case, !good);
}
for (const r of v.tofu.rules) {
  if (r.step === 'bad_cert') continue;
  const t = C.tofuV2(r.stored, r.seen);
  ok('tofu: ' + r.step, t.result === r.result && JSON.stringify(t.store || null) === JSON.stringify(r.store || null));
}
console.log(`VEKTOREN v2 (libsodium.js) ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
process.exit(res.every(Boolean) ? 0 : 1);
