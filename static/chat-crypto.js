// Saga - Chat-Kryptografie, Format v1 (nur libsodium-Primitive ueber libsodium.js; keine eigene Kryptografie).
// Laeuft im Browser (window.SagaChatCrypto, nach static/vendor/libsodium/*) und in Node (module.exports, fuer
// tools/chat_vectors.mjs). Beschreibung des Formats: saga/chat.py (Kopf) und docs/ANLEITUNG.md; Testvektoren:
// tests/chat_vectors.json. Alle Funktionen arbeiten mit Uint8Array; B64 = Standard-Base64 mit Padding.
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.SagaChatCrypto = factory;
})(typeof self !== 'undefined' ? self : this, function (sodium) {
  'use strict';
  var S = sodium, V = 'saga-chat/v1/';
  var B64 = S.base64_variants.ORIGINAL;
  var OPS = 2, MEM = 67108864;                    // crypto_pwhash OPSLIMIT_INTERACTIVE / MEMLIMIT_INTERACTIVE (Argon2id13)
  var enc = new TextEncoder(), dec = new TextDecoder();

  function u8(x) { return typeof x === 'string' ? enc.encode(x) : x; }
  function b64(x) { return S.to_base64(x, B64); }
  function unb64(s, n) {
    var r = S.from_base64(String(s || ''), B64);
    if (n != null && r.length !== n) throw new Error('length');
    return r;
  }
  // LP(f1..fn) = je Feld uint32_be(laenge) || bytes (Strings UTF-8)
  function lp() {
    var parts = Array.prototype.map.call(arguments, u8), len = 0, off = 0;
    parts.forEach(function (p) { len += 4 + p.length; });
    var out = new Uint8Array(len), dv = new DataView(out.buffer);
    parts.forEach(function (p) { dv.setUint32(off, p.length, false); out.set(p, off + 4); off += 4 + p.length; });
    return out;
  }
  function hex(n) { return S.to_hex(S.randombytes_buf(n)); }

  // --- Schluessel ---------------------------------------------------------------------------------------
  function newIdentity() {
    var i = S.crypto_sign_keypair(), b = S.crypto_box_keypair();
    return { ident_pk: i.publicKey, ident_sk: i.privateKey, backup_pk: b.publicKey, backup_sk: b.privateKey };
  }
  function newDevice() {
    var e = S.crypto_box_keypair(), s = S.crypto_sign_keypair();
    return { id: hex(16), enc_pk: e.publicKey, enc_sk: e.privateKey, sign_pk: s.publicKey, sign_sk: s.privateKey };
  }
  function backupMsg(uid, backup_pk) { return lp(V + 'backup', uid, backup_pk); }
  function backupSig(uid, ident_sk, backup_pk) { return S.crypto_sign_detached(backupMsg(uid, backup_pk), ident_sk); }
  function deviceMsg(uid, id, enc_pk, sign_pk) { return lp(V + 'device', uid, id, enc_pk, sign_pk); }
  function deviceSig(uid, ident_sk, d) { return S.crypto_sign_detached(deviceMsg(uid, d.id, d.enc_pk, d.sign_pk), ident_sk); }
  function verify(sig, msg, pk) { try { return S.crypto_sign_verify_detached(sig, msg, pk); } catch (e) { return false; } }
  // Geraet aus der API ({id, enc_pub, sign_pub, sig} in B64) gegen den Identitaetsschluessel pruefen
  function verifyDevice(uid, ident_pk, d) {
    try { return verify(unb64(d.sig, 64), deviceMsg(uid, d.id, unb64(d.enc_pub, 32), unb64(d.sign_pub, 32)), ident_pk); } catch (e) { return false; }
  }
  function verifyBackup(uid, ident_pk, backup_pub, backup_sig) {
    try { return verify(unb64(backup_sig, 64), backupMsg(uid, unb64(backup_pub, 32)), ident_pk); } catch (e) { return false; }
  }

  // --- Wiederherstellungsschluessel (20 Zufallsbytes, Base32 RFC 4648 ohne Padding, 8 Gruppen a 4) --------------
  var A32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  function base32(bytes) {
    var bits = 0, val = 0, out = '';
    for (var i = 0; i < bytes.length; i++) {
      val = (val << 8) | bytes[i]; bits += 8;
      while (bits >= 5) { out += A32[(val >>> (bits - 5)) & 31]; bits -= 5; }
    }
    if (bits > 0) out += A32[(val << (5 - bits)) & 31];
    return out;
  }
  function recoveryKey() { return formatRecovery(base32(S.randombytes_buf(20))); }
  function normRecovery(s) {
    var r = String(s || '').toUpperCase().replace(/[\s-]+/g, '').replace(/0/g, 'O').replace(/1/g, 'I').replace(/8/g, 'B');
    return /^[A-Z2-7]{32}$/.test(r) ? r : null;
  }
  function formatRecovery(s) { return (normRecovery(s) || '').match(/.{4}/g).join('-'); }
  function recoveryKdf(rk, salt, ops, mem) {
    var n = normRecovery(rk); if (!n) throw new Error('recovery key');
    return S.crypto_pwhash(32, enc.encode(n), salt, ops, mem, S.crypto_pwhash_ALG_ARGON2ID13);
  }

  // --- Schluessel-Buendel (Identitaet + Backup), verschluesselt mit dem Wiederherstellungsschluessel ------------
  function bundlePlain(ident_sk, backup_sk) { return JSON.stringify({ v: 1, ident_sk: b64(ident_sk), backup_sk: b64(backup_sk) }); }
  function parsePlain(s) {
    var d = JSON.parse(typeof s === 'string' ? s : dec.decode(s));
    if (d.v !== 1) throw new Error('bundle version');
    var isk = unb64(d.ident_sk, 64), bsk = unb64(d.backup_sk, 32);
    return { ident_sk: isk, ident_pk: S.crypto_sign_ed25519_sk_to_pk(isk), backup_sk: bsk, backup_pk: S.crypto_scalarmult_base(bsk) };
  }
  function sealBundle(uid, rk, ident_sk, backup_sk, opt) {
    opt = opt || {};
    var salt = opt.salt || S.randombytes_buf(16), nonce = opt.nonce || S.randombytes_buf(24), ops = opt.ops || OPS, mem = opt.mem || MEM;
    var key = recoveryKdf(rk, salt, ops, mem);
    var ct = S.crypto_aead_xchacha20poly1305_ietf_encrypt(enc.encode(opt.plain || bundlePlain(ident_sk, backup_sk)), lp(V + 'bundle', uid), null, nonce, key);
    return JSON.stringify({ v: 1, kdf: 'argon2id13', ops: ops, mem: mem, salt: b64(salt), nonce: b64(nonce), ct: b64(ct) });
  }
  function openBundle(uid, rk, bundle) {
    var d = typeof bundle === 'string' ? JSON.parse(bundle) : bundle;
    if (d.v !== 1 || d.kdf !== 'argon2id13') throw new Error('bundle version');
    var key = recoveryKdf(rk, unb64(d.salt, 16), d.ops, d.mem);
    var pt = S.crypto_aead_xchacha20poly1305_ietf_decrypt(null, unb64(d.ct), lp(V + 'bundle', uid), unb64(d.nonce, 24), key);
    return parsePlain(pt);
  }
  function bundleUpdateSig(uid, bundle, ident_sk) { return S.crypto_sign_detached(lp(V + 'bundle-update', uid, bundle), ident_sk); }

  // --- Nachrichten ---------------------------------------------------------------------------------------
  function msgAd(chat_id, id, sender, device, ts, reply_to) { return lp(V + 'msg', String(chat_id), id, sender, device, String(ts), reply_to || ''); }
  function sigMsg(chat_id, id, sender, device, ts, reply_to, body_ct) {
    return lp(V + 'sig', String(chat_id), id, sender, device, String(ts), reply_to || '', S.crypto_generichash(32, unb64(body_ct)));
  }
  // o: {chat_id, sender, device, sign_sk, body (Objekt), reply_to?, recipients: [{id, pk}], id?, ts?, key?, nonce?}
  function encryptMessage(o) {
    var id = o.id || hex(16), ts = o.ts != null ? o.ts : Date.now();
    var K = o.key || S.crypto_aead_xchacha20poly1305_ietf_keygen(), nonce = o.nonce || S.randombytes_buf(24);
    var ct = S.crypto_aead_xchacha20poly1305_ietf_encrypt(enc.encode(o.plain != null ? o.plain : JSON.stringify(o.body)), msgAd(o.chat_id, id, o.sender, o.device, ts, o.reply_to),
      null, nonce, K);
    var raw = new Uint8Array(24 + ct.length); raw.set(nonce, 0); raw.set(ct, 24);
    var body_ct = b64(raw), keys = {};
    (o.recipients || []).forEach(function (r) { keys[r.id] = b64(S.crypto_box_seal(K, r.pk)); });
    var sig = b64(S.crypto_sign_detached(sigMsg(o.chat_id, id, o.sender, o.device, ts, o.reply_to, body_ct), o.sign_sk));
    return { id: id, ts: ts, body_ct: body_ct, sig: sig, keys: keys, reply_to: o.reply_to || null, sender_device: o.device };
  }
  // m: Nachricht aus der API ({id, ts, sender:{username}, sender_device, body_ct, reply_to, key}); pk/sk: Geraete- bzw. Backup-Schluessel
  function openKey(sealed, pk, sk) { return S.crypto_box_seal_open(unb64(sealed, 80), pk, sk); }
  function decryptMessage(chat_id, m, K) {
    var raw = unb64(m.body_ct);
    var pt = S.crypto_aead_xchacha20poly1305_ietf_decrypt(null, raw.subarray(24),
      msgAd(chat_id, m.id, m.sender.username, m.sender_device, m.ts, m.reply_to), raw.subarray(0, 24), K);
    return JSON.parse(dec.decode(pt));
  }
  function verifyMessage(chat_id, m, sign_pk) {
    try { return verify(unb64(m.sig, 64), sigMsg(chat_id, m.id, m.sender.username, m.sender_device, m.ts, m.reply_to, m.body_ct), sign_pk); }
    catch (e) { return false; }
  }

  // --- Anhaenge: Blob = nonce24 || xchacha20poly1305(datei, AD=LP("saga-chat/v1/file", chat_id)) --------------
  function encryptFile(chat_id, bytes, opt) {
    opt = opt || {};
    var K = opt.key || S.crypto_aead_xchacha20poly1305_ietf_keygen(), nonce = opt.nonce || S.randombytes_buf(24);
    var ct = S.crypto_aead_xchacha20poly1305_ietf_encrypt(bytes, lp(V + 'file', String(chat_id)), null, nonce, K);
    var blob = new Uint8Array(24 + ct.length); blob.set(nonce, 0); blob.set(ct, 24);
    return { blob: blob, key: b64(K) };
  }
  function decryptFile(chat_id, blob, key) {
    return S.crypto_aead_xchacha20poly1305_ietf_decrypt(null, blob.subarray(24), lp(V + 'file', String(chat_id)), blob.subarray(0, 24), unb64(key, 32));
  }

  // --- Geraete-Kopplung, Sicherheitsnummer --------------------------------------------------------------------
  // 20 Ziffern (~66 Bit): Der Server sieht die oeffentlichen Schluessel des neuen Geraets und koennte bei einem kurzen
  // Code (6 Ziffern) in Sekundenbruchteilen eigene Schluessel mit gleichem Code erzeugen ("grinding") und sich das
  // Buendel versiegeln lassen. 4 Bloecke a 5 Bytes -> BE-Zahl mod 100000.
  function linkCode(uid, device_id, enc_pk, sign_pk) {
    var h = S.crypto_generichash(32, lp(V + 'link', uid, device_id, enc_pk, sign_pk)), out = [];
    for (var i = 0; i < 4; i++) {
      var v = 0; for (var j = 0; j < 5; j++) v = v * 256 + h[i * 5 + j];
      var s = String(v % 100000); while (s.length < 5) s = '0' + s;
      out.push(s);
    }
    return out.join(' ');
  }
  function cmpBytes(a, b) {
    for (var i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i];
    return a.length - b.length;
  }
  function safetyNumber(uidA, identA, uidB, identB) {
    var a = [u8(uidA), identA], b = [u8(uidB), identB];
    if (cmpBytes(a[0], b[0]) > 0) { var x = a; a = b; b = x; }
    var h = S.crypto_generichash(64, lp(V + 'safety', a[0], a[1], b[0], b[1])), out = [];
    for (var i = 0; i < 12; i++) {
      var v = 0; for (var j = 0; j < 5; j++) v = v * 256 + h[i * 5 + j];       // 40 Bit, passt genau in eine JS-Zahl
      var s = String(v % 100000); while (s.length < 5) s = '0' + s;
      out.push(s);
    }
    return out.join(' ');
  }

  // --- Anrufe, Format saga-call/v1 (saga/calls.py): Signalisierung signiert (Geraet) und fuer das Zielgeraet versiegelt ----
  // sig = sign_detached(LP("saga-call/v1/signal", call_id, from_uid, from_device, to_uid, to_device, body), device_sign_sk)
  // sealed = B64(crypto_box_seal(UTF8(JSON {"v":1,"body":body,"sig":B64(sig)}), ziel_enc_pk)); body = JSON-Text {"t": ...}
  var VC = 'saga-call/v1/';
  function signalMsg(call_id, from_uid, from_device, to_uid, to_device, body) {
    return lp(VC + 'signal', call_id, from_uid, from_device, to_uid, to_device, body);
  }
  // o: {call_id, from_uid, from_device, sign_sk, to_uid, to_device, to_pk, body (Text)}
  function signalSig(o) { return S.crypto_sign_detached(signalMsg(o.call_id, o.from_uid, o.from_device, o.to_uid, o.to_device, o.body), o.sign_sk); }
  function signalInner(body, sig) { return JSON.stringify({ v: 1, body: body, sig: b64(sig) }); }
  function sealSignal(o) { return b64(S.crypto_box_seal(enc.encode(signalInner(o.body, signalSig(o))), o.to_pk)); }
  // -> {body, sig} (wirft bei kaputtem/fremdem Inhalt); pruefen danach mit verifySignal
  function openSignal(sealed, pk, sk) {
    var d = JSON.parse(dec.decode(S.crypto_box_seal_open(unb64(sealed), pk, sk)));
    if (!d || d.v !== 1 || typeof d.body !== 'string') throw new Error('signal version');
    return { body: d.body, sig: unb64(d.sig, 64) };
  }
  // o: {call_id, from_uid, from_device, to_uid, to_device, body}
  function verifySignal(o, sig, sign_pk) { return verify(sig, signalMsg(o.call_id, o.from_uid, o.from_device, o.to_uid, o.to_device, o.body), sign_pk); }
  // DTLS-Fingerabdruecke aus einer SDP: ["sha-256 AB:CD:..."] (Algorithmus klein, Hex gross, sortiert, ohne Doppelte)
  function sdpFingerprints(sdp) {
    var out = {}, re = /^a=fingerprint:(\S+) ([0-9A-Fa-f:]+)\s*$/gm, m;
    while ((m = re.exec(String(sdp || '')))) out[m[1].toLowerCase() + ' ' + m[2].toUpperCase()] = 1;
    return Object.keys(out).sort();
  }

  // --- saga-chat/v2 (2.3): Chats ueber Servergrenzen, gebunden an den foederierten Handle ('~x@host') und chat_ref ----
  // Format: saga/chat_v2.py, docs/APP-API-FED.md; Testvektoren tests/vectors/chat_v2.json. Lokale Chats bleiben v1.
  var V2 = 'saga-chat/v2/';
  function handleMsg(handle, ident_pk, backup_pk) { return lp(V2 + 'handle', handle, ident_pk, backup_pk); }
  function handleSig(handle, ident_sk, ident_pk, backup_pk) { return S.crypto_sign_detached(handleMsg(handle, ident_pk, backup_pk), ident_sk); }
  function deviceMsgV2(handle, id, enc_pk, sign_pk) { return lp(V2 + 'device', handle, id, enc_pk, sign_pk); }
  function deviceSigV2(handle, ident_sk, d) { return S.crypto_sign_detached(deviceMsgV2(handle, d.id, d.enc_pk, d.sign_pk), ident_sk); }
  // Eintrag aus /api/chat/v2/keys ({handle, ident_pub, backup_pub, sig, devices:[{id, enc_pub, sign_pub, fed_sig, revoked}]})
  // selbst pruefen -> {handle, ident_pk, backup_pk, devices (nur gueltige)} oder null (Handle-Zertifikat ungueltig)
  function checkKeysV2(handle, e) {
    try {
      if (!e || e.handle !== handle) return null;
      var ip = unb64(e.ident_pub, 32), bp = unb64(e.backup_pub, 32);
      if (!verify(unb64(e.sig, 64), handleMsg(handle, ip, bp), ip)) return null;
      var devs = (e.devices || []).filter(function (d) {
        try { return verify(unb64(d.fed_sig, 64), deviceMsgV2(handle, d.id, unb64(d.enc_pub, 32), unb64(d.sign_pub, 32)), ip); } catch (x) { return false; }
      });
      return { handle: handle, ident_pk: ip, backup_pk: bp, ident_pub: e.ident_pub, devices: devs };
    } catch (x) { return null; }
  }
  function msgAdV2(ref, id, sender, device, ts, reply_to, ai) { return lp(V2 + 'msg', ref, id, sender, device, String(ts), reply_to || '', ai || ''); }
  function sigMsgV2(ref, id, sender, device, ts, reply_to, ai, body_ct) {
    return lp(V2 + 'sig', ref, id, sender, device, String(ts), reply_to || '', ai || '', S.crypto_generichash(32, unb64(body_ct)));
  }
  // o: {chat_ref, sender_handle, device, sign_sk, plain|body, reply_to?, ai?, recipients:[{id:'dev:<id>@host'|'backup:<handle>', pk}], id?, ts?, key?, nonce?}
  function encryptMessageV2(o) {
    var id = o.id || hex(16), ts = o.ts != null ? o.ts : Date.now();
    var K = o.key || S.crypto_aead_xchacha20poly1305_ietf_keygen(), nonce = o.nonce || S.randombytes_buf(24);
    var ct = S.crypto_aead_xchacha20poly1305_ietf_encrypt(enc.encode(o.plain != null ? o.plain : JSON.stringify(o.body)),
      msgAdV2(o.chat_ref, id, o.sender_handle, o.device, ts, o.reply_to, o.ai), null, nonce, K);
    var raw = new Uint8Array(24 + ct.length); raw.set(nonce, 0); raw.set(ct, 24);
    var body_ct = b64(raw), keys = {};
    (o.recipients || []).forEach(function (r) { keys[r.id] = b64(S.crypto_box_seal(K, r.pk)); });
    var sig = b64(S.crypto_sign_detached(sigMsgV2(o.chat_ref, id, o.sender_handle, o.device, ts, o.reply_to, o.ai, body_ct), o.sign_sk));
    return { v: 2, id: id, ts: ts, body_ct: body_ct, sig: sig, keys: keys, reply_to: o.reply_to || null, ai: o.ai || null,
             sender_handle: o.sender_handle, sender_device: o.device };
  }
  // m: {id, ts, sender_handle, sender_device, body_ct, reply_to, ai}
  function decryptMessageV2(ref, m, K) {
    var raw = unb64(m.body_ct);
    var pt = S.crypto_aead_xchacha20poly1305_ietf_decrypt(null, raw.subarray(24), msgAdV2(ref, m.id, m.sender_handle, m.sender_device, m.ts, m.reply_to, m.ai),
      raw.subarray(0, 24), K);
    return JSON.parse(dec.decode(pt));
  }
  function verifyMessageV2(ref, m, sign_pk) {
    try { return verify(unb64(m.sig, 64), sigMsgV2(ref, m.id, m.sender_handle, m.sender_device, m.ts, m.reply_to, m.ai, m.body_ct), sign_pk); }
    catch (e) { return false; }
  }
  function encryptFileV2(ref, bytes, opt) {
    opt = opt || {};
    var K = opt.key || S.crypto_aead_xchacha20poly1305_ietf_keygen(), nonce = opt.nonce || S.randombytes_buf(24);
    var ct = S.crypto_aead_xchacha20poly1305_ietf_encrypt(bytes, lp(V2 + 'file', ref), null, nonce, K);
    var blob = new Uint8Array(24 + ct.length); blob.set(nonce, 0); blob.set(ct, 24);
    return { blob: blob, key: b64(K) };
  }
  function decryptFileV2(ref, blob, key) {
    return S.crypto_aead_xchacha20poly1305_ietf_decrypt(null, blob.subarray(24), lp(V2 + 'file', ref), blob.subarray(0, 24), unb64(key, 32));
  }
  function safetyNumberV2(handleA, identA, handleB, identB) {
    var a = [u8(handleA), identA], b = [u8(handleB), identB];
    if (cmpBytes(a[0], b[0]) > 0) { var x = a; a = b; b = x; }
    var h = S.crypto_generichash(64, lp(V2 + 'safety', a[0], a[1], b[0], b[1])), out = [];
    for (var i = 0; i < 12; i++) {
      var v = 0; for (var j = 0; j < 5; j++) v = v * 256 + h[i * 5 + j];
      var s = String(v % 100000); while (s.length < 5) s = '0' + s;
      out.push(s);
    }
    return out.join(' ');
  }
  // TOFU-Entscheidung je Handle (Regeln: tests/vectors/chat_v2.json "tofu"): stored = {ident, verified} | null,
  // ident = B64 des gesehenen Identitaetsschluessels -> {result: trust|warn|block, store: neuer Eintrag | null}
  function tofuV2(stored, ident) {
    if (!stored) return { result: 'trust', store: { ident: ident, verified: false } };
    if (stored.ident === ident) return { result: 'trust', store: null };
    if (stored.verified) return { result: 'block', store: null };
    return { result: 'warn', store: { ident: ident, verified: false, changed: true, old: stored.ident } };
  }
  function recipientsV2(entries) {
    var out = [];
    (entries || []).forEach(function (e) {
      var host = e.handle.slice(e.handle.indexOf('@') + 1);
      out.push({ id: 'backup:' + e.handle, pk: e.backup_pk });
      e.devices.forEach(function (d) { if (!d.revoked) out.push({ id: 'dev:' + d.id + '@' + host, pk: unb64(d.enc_pub, 32) }); });
    });
    return out;
  }

  return {
    V2: V2, handleMsg: handleMsg, handleSig: handleSig, deviceMsgV2: deviceMsgV2, deviceSigV2: deviceSigV2, checkKeysV2: checkKeysV2,
    msgAdV2: msgAdV2, sigMsgV2: sigMsgV2, encryptMessageV2: encryptMessageV2, decryptMessageV2: decryptMessageV2,
    verifyMessageV2: verifyMessageV2, encryptFileV2: encryptFileV2, decryptFileV2: decryptFileV2, safetyNumberV2: safetyNumberV2,
    tofuV2: tofuV2, recipientsV2: recipientsV2,
    signalMsg: signalMsg, signalSig: signalSig, signalInner: signalInner, sealSignal: sealSignal, openSignal: openSignal,
    verifySignal: verifySignal, sdpFingerprints: sdpFingerprints,
    VERSION: 1, OPS: OPS, MEM: MEM, b64: b64, unb64: unb64, lp: lp, hex: hex,
    newIdentity: newIdentity, newDevice: newDevice, backupSig: backupSig, deviceSig: deviceSig, deviceMsg: deviceMsg,
    verifyDevice: verifyDevice, verifyBackup: verifyBackup, verify: verify,
    base32: base32, recoveryKey: recoveryKey, normRecovery: normRecovery, formatRecovery: formatRecovery, recoveryKdf: recoveryKdf,
    bundlePlain: bundlePlain, parsePlain: parsePlain, sealBundle: sealBundle, openBundle: openBundle, bundleUpdateSig: bundleUpdateSig,
    msgAd: msgAd, sigMsg: sigMsg, encryptMessage: encryptMessage, openKey: openKey, decryptMessage: decryptMessage,
    verifyMessage: verifyMessage, encryptFile: encryptFile, decryptFile: decryptFile, linkCode: linkCode, safetyNumber: safetyNumber,
    seal: function (msg, pk) { return S.crypto_box_seal(u8(msg), pk); },
    sealOpen: function (ct, pk, sk) { return S.crypto_box_seal_open(ct, pk, sk); },
    text: function (bytes) { return dec.decode(bytes); }
  };
});
