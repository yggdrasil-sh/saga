// Gegenprobe der Testvektoren Chat-Stufe 3 (tests/vectors/chat_v3.json, erzeugt mit PyNaCl) mit libsodium.js aus
// static/vendor/libsodium und static/chat-crypto.js - genau dem Code, den der Browser benutzt.   Aufruf: node tools/chat_vectors_v3.mjs
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
const v = JSON.parse(readFileSync(join(ROOT, 'tests/vectors/chat_v3.json'), 'utf8'));
const res = [];
const ok = (n, c) => { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n); };
const hx = (h) => sodium.from_hex(h), b = C.b64, u = C.unb64, th = (x) => sodium.to_hex(x);

ok('format', v.format === 'saga-chat/v3');
const devs = {}, devOf = {};
for (const [k, d] of Object.entries(v.devices)) {
  const sk = sodium.crypto_sign_seed_keypair(hx(d.sign_seed_hex));
  devs[k] = { id: d.id, sign_pk: sk.publicKey, sign_sk: sk.privateKey }; devOf[d.id] = k;
}
ok('device keys', Object.entries(v.devices).every(([k, d]) => b(devs[k].sign_pk) === d.sign_pk));
ok('identity keys', Object.values(v.identities).every((i) => b(sodium.crypto_sign_seed_keypair(hx(i.ident_seed_hex)).publicKey) === i.ident_pk));
const msgs = Object.assign({}, v.messages, { e1: v.edit.message }, v.edits_bad);
for (const [name, m] of Object.entries(msgs)) {
  const d = devs[devOf[m.sender_device]];
  const e = C.encryptMessageV2({ chat_ref: m.chat_ref, sender_handle: m.sender_handle, device: m.sender_device, sign_sk: d.sign_sk, plain: m.body_json,
    reply_to: m.reply_to, ai: m.ai, recipients: [], id: m.id, ts: m.ts, key: u(m.key), nonce: u(m.nonce) });
  ok(name + ': v2 encrypt/sig', e.body_ct === m.body_ct && e.sig === m.sig && C.verifyMessageV2(m.chat_ref, m, d.sign_pk));
}
for (const [name, r] of Object.entries(v.reactions)) {
  const K = u(r.msg_key), d = devs[devOf[r.dev]];
  ok(name + ': react key', th(C.reactKey(K, r.chat_ref, r.msg_id)) === r.react_key_hex);
  ok(name + ': react ad', th(C.reactAd(r.chat_ref, r.msg_id, r.by, r.dev)) === r.ad_hex);
  const e = C.encryptReaction({ K, chat_ref: r.chat_ref, msg_id: r.msg_id, handle: r.by, device: r.dev, sign_sk: d.sign_sk, emoji: r.emoji, ts: r.ts, nonce: u(r.nonce) });
  ok(name + ': encrypt', e.ct === r.ct && e.sig === r.sig);
  ok(name + ': sig msg + verify', th(C.reactSigMsg(r.chat_ref, r.msg_id, r.by, r.dev, r.ts, r.ct)) === r.sig_msg_hex && C.verifyReaction(r.chat_ref, r.msg_id, r, d.sign_pk));
  ok(name + ': open', C.decryptReaction(K, r.chat_ref, r.msg_id, r) === r.emoji);
}
const ed = v.edit.message, om = v.messages[v.edit.original];
const body = C.decryptMessageV2(ed.chat_ref, ed, u(ed.key));
ok('edit: Inhalt', body.edit === om.id && body.t === v.edit.text && ed.kind === 'edit' && ed.edit_of === om.id);
ok('edit: gueltig (editCheck)', C.editCheck(om, ed, body) === null);
for (const r of v.reject) {
  let good;
  if (r.what === 'reaction') {
    const x = Object.assign({}, v.reactions[r.reaction]);
    ['by', 'dev', 'ts'].forEach((k) => { if (k in r) x[k] = r[k]; });
    const pk = r.sign_pk ? devs[r.sign_pk].sign_pk : devs[devOf[x.dev]].sign_pk;
    good = C.verifyReaction(r.chat_ref || x.chat_ref, r.msg_id || x.msg_id, x, pk);
  } else if (r.what === 'reaction_open' || r.what === 'reaction_body') {
    const mm = v.messages[r.msg], x = r.reaction;
    try { C.decryptReaction(u(mm.key), mm.chat_ref, mm.id, x); good = C.verifyReaction(mm.chat_ref, mm.id, x, devs[devOf[x.dev]].sign_pk); } catch (e) { good = false; }
  } else if (r.what === 'edit') {
    const e = msgs[r.edit], o = v.messages[r.original];
    const why = C.editCheck(o, e, C.decryptMessageV2(e.chat_ref, e, u(e.key)));
    ok('reject: ' + r.case + ' (Grund ' + r.reason + ')', why === r.reason);
    continue;
  } else {
    const e = msgs[r.edit];
    good = C.verifyMessageV2(r.chat_ref, e, devs[devOf[e.sender_device]].sign_pk);
  }
  ok('reject: ' + r.case, !good);
}
// Seiten-Funktion des Chat-Workers (openReactions / openReactionsAsync mit WebCrypto): gueltige oeffnen, abgewiesene nicht
{
  const jobs = [], bad = [], pks = {};
  for (const [name, r] of Object.entries(v.reactions)) {
    pks[r.by + '|' + r.dev] = C.b64(devs[devOf[r.dev]].sign_pk);
    jobs.push({ K: u(r.msg_key), ref: r.chat_ref, mid: r.msg_id, items: [r], want: r.emoji, name });
  }
  for (const r of v.reject.filter((x) => x.what === 'reaction')) {
    const x = Object.assign({}, v.reactions[r.reaction]);
    ['by', 'dev', 'ts'].forEach((k) => { if (k in r) x[k] = r[k]; });
    const pk = r.sign_pk ? devs[r.sign_pk].sign_pk : devs[devOf[x.dev]] && devs[devOf[x.dev]].sign_pk;
    if (!pk) continue;
    const pm = { [x.by + '|' + x.dev]: C.b64(pk) };
    bad.push({ job: { K: u(v.reactions[r.reaction].msg_key), ref: r.chat_ref || x.chat_ref, mid: r.msg_id || x.msg_id, items: [x], pks: pm }, name: r.case });
  }
  const good = (o, j) => j.want === '' ? !(o[j.mid] || []).length : (o[j.mid] || []).some((x) => x.e === j.want);
  const none = (o, b) => !(o[b.job.mid] || []).length;
  ok('openReactions: gueltige geoeffnet (' + jobs.length + ')', jobs.every((j) => good(C.openReactions([j], pks), j)));
  ok('openReactions: abgewiesene nicht (' + bad.length + ')', bad.length > 0 && bad.every((b) => none(C.openReactions([b.job]), b)));
  let ga = true, na = true;
  for (const j of jobs) ga = ga && good(await C.openReactionsAsync([j], pks), j);
  for (const b of bad) na = na && none(await C.openReactionsAsync([b.job]), b);
  ok('openReactionsAsync (WebCrypto): gueltige geoeffnet', ga);
  ok('openReactionsAsync (WebCrypto): abgewiesene nicht', na);
}
for (const r of v.tofu_migrate.rules) {
  const t = C.tofuMigrate(r.v1, r.seen, r.ver);
  ok('tofu_migrate: ' + r.step, t.result === r.result && JSON.stringify(t.store || null) === JSON.stringify(r.store || null));
}
console.log(`VEKTOREN v3 (libsodium.js) ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
process.exit(res.every(Boolean) ? 0 : 1);
