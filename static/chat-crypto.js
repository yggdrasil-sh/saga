// Saga - Chat-Kryptografie, Format v1 (nur libsodium-Primitive ueber libsodium.js; keine eigene Kryptografie).
// Laeuft im Browser (window.SagaChatCrypto, nach static/vendor/libsodium/*) und in Node (module.exports, fuer
// tools/chat_vectors.mjs). Beschreibung des Formats: saga/chat.py (Kopf) und docs/ANLEITUNG.md; Testvektoren:
// tests/chat_vectors.json. Alle Funktionen arbeiten mit Uint8Array; B64 = Standard-Base64 mit Padding.
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.SagaChatCrypto = factory;
  // saga-file/v2 (2.4): Auftrag fuer viele Bloecke am Stueck (Worker static/chat-worker.js, sonst im Fenster). X = SagaChatCrypto(sodium)
  //   {op:'f2enc', key, prefix, cs, size, i, bytes} -> {ct}   {op:'f2dec', key, head, i, ct} -> {plain}
  root.SagaF2Job = function (X, d) {
    var K = typeof d.key === 'string' ? X.unb64(d.key, 32) : new Uint8Array(d.key), cs, info, out, n, k, off;
    if (d.op === 'f2enc') {
      cs = d.cs; info = { cs: cs, size: d.size, n: X.f2Blocks(d.size, cs), prefix: new Uint8Array(d.prefix) };
      var pb = new Uint8Array(d.bytes), parts = [], len = 0, nb = Math.max(1, Math.ceil(pb.length / cs));
      for (k = 0; k < nb; k++) {
        var c = X.f2Seal(K, info, d.i + k, pb.subarray(k * cs, Math.min((k + 1) * cs, pb.length)));
        parts.push(c); len += c.length;
      }
      out = new Uint8Array(len); n = 0;
      parts.forEach(function (p) { out.set(p, n); n += p.length; });
      return { ct: out.buffer };
    }
    info = X.f2Parse(new Uint8Array(d.head)); cs = info.cs;
    var cb = new Uint8Array(d.ct), plain = [], pl = 0;
    for (k = 0, off = 0; off < cb.length; k++, off += cs + 16) {
      var p = X.f2Open(K, info, d.i + k, cb.subarray(off, Math.min(off + cs + 16, cb.length)));
      plain.push(p); pl += p.length;
    }
    out = new Uint8Array(pl); n = 0;
    plain.forEach(function (p) { out.set(p, n); n += p.length; });
    return { plain: out.buffer };
  };
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

  // --- saga-file/v2 (2.4, Videos im Chat): blockweise, wahlfreier Zugriff. Format: saga/file_v2.py, Vektoren:
  // tests/vectors/file_v2.json. Kopf(48) || Block_0 .. Block_{N-1}; Block_i = xchacha20poly1305(plain_i, AD_i, prefix16||u64(i), K)
  var F2 = { HEADER: 48, TAG: 16, CS: 262144 };
  function u64(n) {                                    // u64 big-endian (Zahlen bis 2^53)
    var b = new Uint8Array(8), dv = new DataView(b.buffer);
    dv.setUint32(0, Math.floor(n / 4294967296), false); dv.setUint32(4, n >>> 0, false); return b;
  }
  function u32(n) { var b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, n >>> 0, false); return b; }
  function f2Blocks(size, cs) { cs = cs || F2.CS; return Math.max(1, Math.ceil(size / cs)); }
  function f2Size(size, cs) { cs = cs || F2.CS; return F2.HEADER + size + F2.TAG * f2Blocks(size, cs); }
  function f2Off(i, cs) { return F2.HEADER + i * ((cs || F2.CS) + F2.TAG); }
  function f2Header(size, prefix, cs) {
    cs = cs || F2.CS;
    var h = new Uint8Array(48), dv = new DataView(h.buffer);
    h.set([0x53, 0x47, 0x46, 0x32, 2, 0], 0); dv.setUint32(8, cs, false); h.set(u64(size), 16); h.set(prefix, 24);
    return h;
  }
  function f2Parse(h) {                                 // -> {cs, size, n, prefix}; Error bei falschem Kopf
    if (!h || h.length < 48 || h[0] !== 0x53 || h[1] !== 0x47 || h[2] !== 0x46 || h[3] !== 0x32 || h[4] !== 2 || h[5] !== 0) throw new Error('header');
    var dv = new DataView(h.buffer, h.byteOffset, 48), cs = dv.getUint32(8, false);
    if (dv.getUint16(6, false) || dv.getUint32(12, false) || cs < 65536 || cs > 1048576 || (cs & (cs - 1))) throw new Error('header');
    for (var k = 40; k < 48; k++) if (h[k]) throw new Error('header');
    var size = dv.getUint32(16, false) * 4294967296 + dv.getUint32(20, false);
    return { cs: cs, size: size, n: f2Blocks(size, cs), prefix: h.slice(24, 40) };
  }
  function f2Nonce(prefix, i) { var n = new Uint8Array(24); n.set(prefix, 0); n.set(u64(i), 16); return n; }
  function f2Ad(i, n, cs, size) { return lp('saga-file/v2', u64(i), u64(n), u32(cs), u64(size), new Uint8Array([i === n - 1 ? 1 : 0])); }
  function f2Seal(K, info, i, plain) { return S.crypto_aead_xchacha20poly1305_ietf_encrypt(plain, f2Ad(i, info.n, info.cs, info.size), null, f2Nonce(info.prefix, i), K); }
  function f2Open(K, info, i, ct) {
    if (i < 0 || i >= info.n) throw new Error('block');
    return S.crypto_aead_xchacha20poly1305_ietf_decrypt(null, ct, f2Ad(i, info.n, info.cs, info.size), f2Nonce(info.prefix, i), K);
  }
  // Klartext-Bereich a..b (einschliesslich) -> {i, j, from, to} (Bloecke i..j, Ciphertext-Bytes from..to einschliesslich)
  function f2Range(size, a, b, cs) {
    cs = cs || F2.CS; var i = Math.floor(a / cs), j = Math.floor(b / cs);
    return { i: i, j: j, from: f2Off(i, cs), to: Math.min(f2Off(j, cs) + cs + F2.TAG, f2Size(size, cs)) - 1 };
  }
  // Streaming beim Senden: e = f2Encryptor(size); e.header; e.seal(i, plain_i) je Block (plain_i = cs Bytes, letzter kuerzer)
  function f2Encryptor(size, opt) {
    opt = opt || {};
    var K = opt.key || S.crypto_aead_xchacha20poly1305_ietf_keygen(), prefix = opt.prefix || S.randombytes_buf(16), cs = opt.cs || F2.CS;
    var info = { cs: cs, size: size, n: f2Blocks(size, cs), prefix: prefix };
    return { key: b64(K), info: info, header: f2Header(size, prefix, cs), seal: function (i, plain) { return f2Seal(K, info, i, plain); } };
  }
  function encryptFileF2(bytes, opt) {
    var e = f2Encryptor(bytes.length, opt), cs = e.info.cs, out = new Uint8Array(f2Size(bytes.length, cs));
    out.set(e.header, 0);
    for (var i = 0; i < e.info.n; i++) out.set(e.seal(i, bytes.subarray(i * cs, Math.min((i + 1) * cs, bytes.length))), f2Off(i, cs));
    return { blob: out, key: e.key };
  }
  function decryptFileF2(blob, key) {
    var info = f2Parse(blob.subarray(0, 48)), K = typeof key === 'string' ? unb64(key, 32) : key;
    if (blob.length !== f2Size(info.size, info.cs)) throw new Error('size');
    var out = new Uint8Array(info.size);
    for (var i = 0; i < info.n; i++) {
      var a = f2Off(i, info.cs), p = f2Open(K, info, i, blob.subarray(a, Math.min(a + info.cs + F2.TAG, blob.length)));
      out.set(p, i * info.cs);
    }
    return out;
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
  // --- Anrufe saga-call/v2 (2.3, saga/fed_calls.py): wie v1, aber an die foederierten Handles gebunden -------------------
  // sig = sign_detached(LP("saga-call/v2/signal", call_id, from_handle, from_device, to_handle, to_device, body), device_sign_sk)
  // sealed = B64(crypto_box_seal(UTF8(JSON {"v":2,"body":body,"sig":B64(sig)}), ziel_enc_pk))
  var VC2 = 'saga-call/v2/';
  function signalMsgV2(call_id, from_handle, from_device, to_handle, to_device, body) {
    return lp(VC2 + 'signal', call_id, from_handle, from_device, to_handle, to_device, body);
  }
  // o: {call_id, from_handle, from_device, sign_sk, to_handle, to_device, to_pk, body}
  function sealSignalV2(o) {
    var sig = S.crypto_sign_detached(signalMsgV2(o.call_id, o.from_handle, o.from_device, o.to_handle, o.to_device, o.body), o.sign_sk);
    return b64(S.crypto_box_seal(enc.encode(JSON.stringify({ v: 2, body: o.body, sig: b64(sig) })), o.to_pk));
  }
  function openSignalV2(sealed, pk, sk) {
    var d = JSON.parse(dec.decode(S.crypto_box_seal_open(unb64(sealed), pk, sk)));
    if (!d || d.v !== 2 || typeof d.body !== 'string') throw new Error('signal version');
    return { body: d.body, sig: unb64(d.sig, 64) };
  }
  // o: {call_id, from_handle, from_device, to_handle, to_device, body}
  function verifySignalV2(o, sig, sign_pk) { return verify(sig, signalMsgV2(o.call_id, o.from_handle, o.from_device, o.to_handle, o.to_device, o.body), sign_pk); }
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
  // Signierte Geraete-Liste: dsig = sign_detached(LP(V2+"devices", handle, ver, ids sortiert mit ","), ident_sk)
  function deviceSetMsg(handle, ver, ids) { return lp(V2 + 'devices', handle, String(ver), ids.slice().sort().join(',')); }
  function deviceSetSig(handle, ident_sk, ver, ids) { return { ver: ver, ids: ids.slice().sort(), sig: b64(S.crypto_sign_detached(deviceSetMsg(handle, ver, ids), ident_sk)) }; }
  function verifyDeviceSet(handle, ip, ds) {
    try {
      if (!ds || typeof ds.ver !== 'number' || ds.ver < 1 || !Array.isArray(ds.ids)) return false;
      return verify(unb64(ds.sig, 64), deviceSetMsg(handle, ds.ver, ds.ids), ip);
    } catch (x) { return false; }
  }
  // Eintrag aus /api/chat/v2/keys ({handle, ident_pub, backup_pub, sig, dset:{ver, ids, sig}, devices:[{id, enc_pub, sign_pub, fed_sig,
  // revoked}]}) selbst pruefen -> {handle, ident_pk, backup_pk, ver, devices (nur gueltige)} oder null (Handle-Zertifikat ungueltig).
  // Aktiv ist ein Geraet nur, wenn es in der signierten Geraete-Liste steht (und der Server es nicht widerrufen meldet).
  function checkKeysV2(handle, e) {
    try {
      if (!e || e.handle !== handle) return null;
      var ip = unb64(e.ident_pub, 32), bp = unb64(e.backup_pub, 32);
      if (!verify(unb64(e.sig, 64), handleMsg(handle, ip, bp), ip)) return null;
      var ds = e.dset && verifyDeviceSet(handle, ip, e.dset) ? e.dset : null;
      var devs = (e.devices || []).filter(function (d) {
        try { return verify(unb64(d.fed_sig, 64), deviceMsgV2(handle, d.id, unb64(d.enc_pub, 32), unb64(d.sign_pub, 32)), ip); } catch (x) { return false; }
      }).map(function (d) {
        return Object.assign({}, d, { revoked: !!d.revoked || (!!ds && ds.ids.indexOf(d.id) < 0) });
      });
      return { handle: handle, ident_pk: ip, backup_pk: bp, ident_pub: e.ident_pub, ver: ds ? ds.ver : 0, signed: !!ds, devices: devs };
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
  // TOFU-Entscheidung je Handle (Regeln: tests/vectors/chat_v2.json "tofu"): stored = {ident, verified, changed?, old?, dver} | null,
  // ident = B64 des gesehenen Identitaetsschluessels, ver = Version der signierten Geraete-Liste
  // -> {result: trust|warn|block|reject, store: neuer Eintrag | null}. Eine Warnung bleibt, bis die Person sie bestaetigt
  // (Hintergrund-Abrufe verbrauchen sie nicht); eine aeltere Geraete-Liste als schon gesehen wird abgelehnt.
  function tofuV2(stored, ident, ver) {
    ver = ver || 0;
    if (!stored) return { result: 'trust', store: { ident: ident, verified: false, dver: ver } };
    if (stored.ident === ident) {
      if (ver < (stored.dver || 0)) return { result: 'reject', store: null };
      var up = ver > (stored.dver || 0) ? Object.assign({}, stored, { dver: ver }) : null;
      return { result: stored.changed ? 'warn' : 'trust', store: up };
    }
    if (stored.verified) return { result: 'block', store: null };
    return { result: 'warn', store: { ident: ident, verified: false, changed: true, old: stored.ident, dver: ver } };
  }
  // Gelesen-Bestaetigung (2.4): body = JSON-Text {id, n, ts}, signiert vom Geraet, versiegelt fuer den Backup-Schluessel des
  // Gegenuebers. v1: chat = String(chat_id), reader = Benutzername; v2: chat = chat_ref, reader = Handle.
  function receiptMsg(chat, reader, dev, body, v2) { return lp((v2 ? V2 : V) + 'receipt', String(chat), reader, dev, body); }
  function sealReceipt(o) {
    var body = JSON.stringify(o.body), sig = S.crypto_sign_detached(receiptMsg(o.chat, o.reader, o.device, body, o.v2), o.sign_sk);
    return b64(S.crypto_box_seal(enc.encode(JSON.stringify({ v: 1, dev: o.device, body: body, sig: b64(sig) })), o.to_pk));
  }
  function openReceipt(sealed, pk, sk) {
    var d = JSON.parse(dec.decode(S.crypto_box_seal_open(unb64(sealed), pk, sk)));
    if (d.v !== 1 || typeof d.body !== 'string' || typeof d.dev !== 'string') throw new Error('receipt version');
    return { dev: d.dev, body: d.body, sig: unb64(d.sig, 64) };
  }
  function verifyReceipt(chat, reader, r, sign_pk, v2) { return verify(r.sig, receiptMsg(chat, reader, r.dev, r.body, v2), sign_pk); }
  function recipientsV2(entries) {
    var out = [];
    (entries || []).forEach(function (e) {
      var host = e.handle.slice(e.handle.indexOf('@') + 1);
      out.push({ id: 'backup:' + e.handle, pk: e.backup_pk });
      e.devices.forEach(function (d) { if (!d.revoked) out.push({ id: 'dev:' + d.id + '@' + host, pk: unb64(d.enc_pub, 32) }); });
    });
    return out;
  }

  // --- Stufe 3 (2.5, saga/chat_v3.py, Vektoren tests/vectors/chat_v3.json): Reaktionen, Bearbeiten, Vertrauens-Uebernahme ---
  // Reaktion: K_r = generichash(32, LP(V3+'react-key', chat_ref, msg_id), key = K der Nachricht); body = {"e", "ts"};
  // ct = B64(nonce24 || xchacha20poly1305(body, LP(V3+'react', ref, mid, handle, geraet), nonce, K_r));
  // sig = sign_detached(LP(V3+'react-sig', ref, mid, handle, geraet, ts, generichash32(raw(ct))), device_sign_sk)
  var V3 = 'saga-chat/v3/', EMOJI_MAX = 32, EDIT_WINDOW = 86400;
  function reactKey(K, ref, mid) { return S.crypto_generichash(32, lp(V3 + 'react-key', ref, mid), K); }
  function reactAd(ref, mid, handle, device) { return lp(V3 + 'react', ref, mid, handle, device); }
  function reactSigMsg(ref, mid, handle, device, ts, ct) {
    return lp(V3 + 'react-sig', ref, mid, handle, device, String(ts), S.crypto_generichash(32, unb64(ct)));
  }
  // o = {K, chat_ref, msg_id, handle, device, sign_sk, emoji ('' = zuruecknehmen), ts?, nonce?} -> {by, dev, ts, ct, sig}
  function encryptReaction(o) {
    var ts = o.ts != null ? o.ts : Date.now(), nonce = o.nonce || S.randombytes_buf(24);
    var body = enc.encode(JSON.stringify({ e: o.emoji, ts: ts }));
    var c = S.crypto_aead_xchacha20poly1305_ietf_encrypt(body, reactAd(o.chat_ref, o.msg_id, o.handle, o.device), null, nonce,
                                                         reactKey(o.K, o.chat_ref, o.msg_id));
    var all = new Uint8Array(24 + c.length); all.set(nonce, 0); all.set(c, 24);
    var ct = b64(all);
    return { by: o.handle, dev: o.device, ts: ts, ct: ct, sig: b64(S.crypto_sign_detached(reactSigMsg(o.chat_ref, o.msg_id, o.handle, o.device, ts, ct), o.sign_sk)) };
  }
  function verifyReaction(ref, mid, r, sign_pk) {
    try { return verify(unb64(r.sig, 64), reactSigMsg(ref, mid, r.by, r.dev, r.ts, r.ct), sign_pk); } catch (e) { return false; }
  }
  // -> Emoji ('' = zurueckgenommen); Fehler, wenn Schluessel, Bindung oder Inhalt nicht passen
  function decryptReaction(K, ref, mid, r) {
    var raw = unb64(r.ct);
    var pt = dec.decode(S.crypto_aead_xchacha20poly1305_ietf_decrypt(null, raw.subarray(24), reactAd(ref, mid, r.by, r.dev), raw.subarray(0, 24),
                                                                      reactKey(K, ref, mid)));
    var e = reactBody(pt, r.ts);
    if (e === null) throw new Error('reaction body');
    return e;
  }
  function reactBody(pt, ts) {
    var d;
    try { d = JSON.parse(pt); } catch (e) { return null; }
    if (!d || typeof d !== 'object' || Object.keys(d).sort().join() !== 'e,ts' || d.ts !== ts || typeof d.e !== 'string') return null;
    if (enc.encode(d.e).length > EMOJI_MAX || /[\u0000-\u001f\u007f-\u009f]/.test(d.e)) return null;
    return d.e;
  }
  // Bearbeitung (normale v2-Nachricht kind 'edit' mit edit_of, Inhalt {edit, t}) gegen das Original pruefen -> null | Grund
  function editCheck(orig, edit, body) {
    if (!body || typeof body.t !== 'string' || body.edit !== edit.edit_of || edit.edit_of !== orig.id) return 'edit_of_mismatch';
    if (!edit.sender_handle || edit.sender_handle !== orig.sender_handle) return 'other_sender';
    if (orig.ai) return 'ai_original';
    if ((edit.created || 0) - (orig.created || 0) > EDIT_WINDOW) return 'window';
    return null;
  }
  // Reaktionen einer Seite pruefen und oeffnen (Worker oder Fenster): jobs = [{K, ref, mid, items: [{by, dev, ts, ct, sig}],
  // pks?}], pks = {'<handle>|<geraet>': sign_pk} (je Auftrag oder gemeinsam) -> {mid: [{by, e, ts}]}; ungueltige fallen weg, je Person die neueste (ts)
  // Ergebnis-Zwischenspeicher (je Worker/Fenster): gleiche Reaktion (Chat, Nachricht, Nachrichtenschluessel, Person, Geraet,
  // ts, sig, ct, Signaturschluessel)
  // nicht zweimal pruefen - beim Neuzeichnen und bei Live-Ereignissen kommen meist dieselben wieder.
  // Je Person zaehlt nur ihre neueste Reaktion (der Server haelt ohnehin nur eine); ist die ungueltig, faellt die Person weg.
  var RXC = new Map(), PKC = new Map();
  function lpCat(pre, parts) {                    // = lp(<Felder von pre>, ...parts), pre schon als lp-Bytes
    var ps = parts.map(u8), len = pre.length;
    ps.forEach(function (x) { len += 4 + x.length; });
    var out = new Uint8Array(len), dv = new DataView(out.buffer), off = pre.length;
    out.set(pre, 0);
    ps.forEach(function (x) { dv.setUint32(off, x.length, false); out.set(x, off + 4); off += 4 + x.length; });
    return out;
  }
  function rxPlan(jobs, pks) {
    if (RXC.size > 20000) RXC.clear();
    if (PKC.size > 2000) PKC.clear();
    var plan = [];
    (jobs || []).forEach(function (j) {
      var newest = {}, pm = j.pks || pks || {}, jk = j.K ? j.ref + '|' + j.mid + '|' + b64(j.K) : '';   // Bindung wie beim Pruefen
      (j.items || []).forEach(function (r) { if (!newest[r.by] || newest[r.by].ts < r.ts) newest[r.by] = r; });
      var p = { j: j, kr: null, out: [], todo: [], sp: lp(V3 + 'react-sig', j.ref, j.mid), ap: lp(V3 + 'react', j.ref, j.mid) };
      Object.keys(newest).forEach(function (by) {
        var r = newest[by], k64 = pm[r.by + '|' + r.dev];
        if (!k64 || !j.K) return;
        if (typeof k64 !== 'string') k64 = b64(k64);
        var ck = jk + '|' + r.by + '|' + r.dev + '|' + r.ts + '|' + r.sig + '|' + r.ct + '|' + k64, e = RXC.get(ck);
        if (e !== undefined) { p.out.push({ by: r.by, e: e, ts: r.ts }); return; }
        var pk = PKC.get(k64);
        if (!pk) { try { pk = unb64(k64, 32); } catch (x) { return; } PKC.set(k64, pk); }
        var raw; try { raw = unb64(r.ct); } catch (x) { return; }
        if (raw.length < 40) return;
        p.todo.push({ r: r, ck: ck, pk: pk, k64: k64, raw: raw });
      });
      plan.push(p);
    });
    return plan;
  }
  function rxOpen1(p, t) {                       // nach gueltiger Signatur: oeffnen und merken
    var j = p.j, r = t.r, e, raw = t.raw;
    try {
      if (!p.kr) p.kr = reactKey(j.K, j.ref, j.mid);     // einmal je Nachricht
      e = reactBody(dec.decode(S.crypto_aead_xchacha20poly1305_ietf_decrypt(null, raw.subarray(24), lpCat(p.ap, [r.by, r.dev]),
                                                                             raw.subarray(0, 24), p.kr)), r.ts);
    } catch (x) { return; }
    if (e === null) return;
    RXC.set(t.ck, e); p.out.push({ by: r.by, e: e, ts: r.ts });
  }
  function rxSig(p, t) { return lpCat(p.sp, [t.r.by, t.r.dev, String(t.r.ts), S.crypto_generichash(32, t.raw)]); }
  function rxDone(plan) {
    var out = {};
    plan.forEach(function (p) { out[p.j.mid] = p.out.filter(function (x) { return x.e; }); });
    return out;
  }
  function openReactions(jobs, pks) {             // synchron (libsodium.js)
    var plan = rxPlan(jobs, pks);
    plan.forEach(function (p) { p.todo.forEach(function (t) { if (rxVerify(p, t)) rxOpen1(p, t); }); });
    return rxDone(plan);
  }
  function rxVerify(p, t) { try { return verify(unb64(t.r.sig, 64), rxSig(p, t), t.pk); } catch (e) { return false; } }
  // Wie openReactions, Signaturen aber ueber WebCrypto Ed25519 (nativ, deutlich schneller), wenn der Browser es kann -> Promise
  var WCK = new Map(), wcOk = null;
  function wcEd() {
    if (wcOk) return wcOk;
    var sub = typeof crypto !== 'undefined' && crypto.subtle;
    if (!sub) return (wcOk = Promise.resolve(false));
    var kp = S.crypto_sign_keypair(), m = new Uint8Array([1, 2, 3]), sg = S.crypto_sign_detached(m, kp.privateKey);
    wcOk = sub.importKey('raw', kp.publicKey, { name: 'Ed25519' }, false, ['verify'])
      .then(function (k) { return sub.verify({ name: 'Ed25519' }, k, sg, m); }).then(function (x) { return x === true; }, function () { return false; });
    return wcOk;
  }
  function openReactionsAsync(jobs, pks) {
    var plan = rxPlan(jobs, pks), n = 0;
    plan.forEach(function (p) { n += p.todo.length; });
    if (!n) return Promise.resolve(rxDone(plan));
    return wcEd().then(function (ok) {
      if (!ok) { plan.forEach(function (p) { p.todo.forEach(function (t) { if (rxVerify(p, t)) rxOpen1(p, t); }); }); return rxDone(plan); }
      if (WCK.size > 2000) WCK.clear();
      var need = {};                                   // erst die Schluessel (je Geraet einmal), dann alle Pruefungen auf einmal
      plan.forEach(function (p) { p.todo.forEach(function (t) {
        if (!WCK.has(t.k64)) WCK.set(t.k64, crypto.subtle.importKey('raw', t.pk, { name: 'Ed25519' }, false, ['verify']));
        need[t.k64] = WCK.get(t.k64);
      }); });
      var keys = {};
      return Promise.all(Object.keys(need).map(function (k) { return need[k].then(function (x) { keys[k] = x; }, function () { /* ungueltig */ }); })).then(function () {
        var all = [];
        plan.forEach(function (p) {
          p.todo.forEach(function (t) {
            var key = keys[t.k64], msg, sig;
            if (!key) return;
            try { msg = rxSig(p, t); sig = unb64(t.r.sig, 64); } catch (x) { return; }
            all.push(crypto.subtle.verify({ name: 'Ed25519' }, key, sig, msg).then(function (v) { if (v === true) rxOpen1(p, t); }, function () { /* ungueltig */ }));
          });
        });
        return Promise.all(all);
      }).then(function () { return rxDone(plan); });
    });
  }
  // Vertrauens-Uebernahme trust:<benutzer> (v1) -> trust2:<handle>, nur ohne trust2-Eintrag; gleiche Regeln wie tofuV2
  function tofuMigrate(v1, ident, ver) {
    ver = ver || 0;
    if (!v1) return { result: 'trust', store: { ident: ident, verified: false, dver: ver } };
    if (v1.ident === ident) {
      var st = { ident: ident, verified: !!v1.verified, dver: ver };
      if (v1.changed) { st.changed = true; st.old = v1.old; return { result: 'warn', store: st }; }
      return { result: 'trust', store: st };
    }
    if (v1.verified) return { result: 'block', store: null };
    return { result: 'warn', store: { ident: ident, verified: false, changed: true, old: v1.ident, dver: ver } };
  }

  return {
    V2: V2, handleMsg: handleMsg, handleSig: handleSig, deviceMsgV2: deviceMsgV2, deviceSigV2: deviceSigV2, checkKeysV2: checkKeysV2,
    msgAdV2: msgAdV2, sigMsgV2: sigMsgV2, encryptMessageV2: encryptMessageV2, decryptMessageV2: decryptMessageV2,
    verifyMessageV2: verifyMessageV2, encryptFileV2: encryptFileV2, decryptFileV2: decryptFileV2, safetyNumberV2: safetyNumberV2,
    tofuV2: tofuV2, recipientsV2: recipientsV2, deviceSetMsg: deviceSetMsg, deviceSetSig: deviceSetSig, verifyDeviceSet: verifyDeviceSet,
    signalMsg: signalMsg, signalSig: signalSig, signalInner: signalInner, sealSignal: sealSignal, openSignal: openSignal,
    verifySignal: verifySignal, sdpFingerprints: sdpFingerprints,
    receiptMsg: receiptMsg, sealReceipt: sealReceipt, openReceipt: openReceipt, verifyReceipt: verifyReceipt,
    signalMsgV2: signalMsgV2, sealSignalV2: sealSignalV2, openSignalV2: openSignalV2, verifySignalV2: verifySignalV2,
    V3: V3, reactKey: reactKey, reactAd: reactAd, reactSigMsg: reactSigMsg, encryptReaction: encryptReaction, verifyReaction: verifyReaction,
    decryptReaction: decryptReaction, openReactions: openReactions, openReactionsAsync: openReactionsAsync, reactBody: reactBody, editCheck: editCheck, tofuMigrate: tofuMigrate,
    VERSION: 1, OPS: OPS, MEM: MEM, b64: b64, unb64: unb64, lp: lp, hex: hex,
    newIdentity: newIdentity, newDevice: newDevice, backupSig: backupSig, deviceSig: deviceSig, deviceMsg: deviceMsg,
    verifyDevice: verifyDevice, verifyBackup: verifyBackup, verify: verify,
    base32: base32, recoveryKey: recoveryKey, normRecovery: normRecovery, formatRecovery: formatRecovery, recoveryKdf: recoveryKdf,
    bundlePlain: bundlePlain, parsePlain: parsePlain, sealBundle: sealBundle, openBundle: openBundle, bundleUpdateSig: bundleUpdateSig,
    msgAd: msgAd, sigMsg: sigMsg, encryptMessage: encryptMessage, openKey: openKey, decryptMessage: decryptMessage,
    verifyMessage: verifyMessage, encryptFile: encryptFile, decryptFile: decryptFile, linkCode: linkCode, safetyNumber: safetyNumber,
    F2: F2, f2Blocks: f2Blocks, f2Size: f2Size, f2Off: f2Off, f2Header: f2Header, f2Parse: f2Parse, f2Nonce: f2Nonce, f2Ad: f2Ad,
    f2Seal: f2Seal, f2Open: f2Open, f2Range: f2Range, f2Encryptor: f2Encryptor, encryptFileF2: encryptFileF2, decryptFileF2: decryptFileF2,
    seal: function (msg, pk) { return S.crypto_box_seal(u8(msg), pk); },
    sealOpen: function (ct, pk, sk) { return S.crypto_box_seal_open(ct, pk, sk); },
    text: function (bytes) { return dec.decode(bytes); }
  };
});
