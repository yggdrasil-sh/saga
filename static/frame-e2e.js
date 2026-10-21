// Saga 2.5 Gjallarhorn: Frame-E2E "saga-horn/v1" (Vertrag docs/HORN-E2E.md, Testvektoren tests/vectors/horn_e2e.json).
// Eine Datei, drei Rollen:
//   - Worker (new Worker('/static/frame-e2e.js'), CSP worker-src 'self'): RTCRtpScriptTransform (onrtctransform) bzw.
//     Rueckfall createEncodedStreams (op "stream"), Ver-/Entschluesseln je codiertem Rahmen, Schluesselwechsel, Wiederholungsschutz.
//   - Fenster (<script src>): window.SagaFrameE2E.create() haengt Sender/Empfaenger an den Worker und spricht mit ihm.
//   - Node (module.exports): derselbe Kern fuer tools/horn_e2e_vectors.mjs.
// Grundsatz: ohne passenden Schluessel KEINE Ausgabe - nie Klartext durchreichen, auch nicht bei Fehlern.
// Rahmen = Kopf(h, unverschluesselt, AAD) || AES-128-GCM(Rest) || Tag(16) || IV(12) || 0x0C || kid  (FrameCryptor-Format)
(function (root) {
  'use strict';
  var subtle = (root.crypto || {}).subtle;
  var enc = new TextEncoder();
  var FRAME_SALT = enc.encode('saga-horn/v1/frame'), RATCHET_SALT = enc.encode('saga-horn-ratchet');
  var INFO0 = new Uint8Array(128), EMPTY = new Uint8Array(0);
  var C = { IV: 12, TAG: 16, TRAILER: 14, RING: 16, WINDOW: 128, AHEAD: 4, SWITCH_MS: 1500, GRACE_MS: 3000, NEED_MS: 2000, NEED_EVERY: 5000,
            KEEP: 3, MAX_N: 0x7fffffff };

  // --- Hilfen -------------------------------------------------------------------------------------------------------
  function hex(u) { var s = ''; for (var i = 0; i < u.length; i++) s += (u[i] < 16 ? '0' : '') + u[i].toString(16); return s; }
  function unhex(s) { var u = new Uint8Array(s.length >> 1); for (var i = 0; i < u.length; i++) u[i] = parseInt(s.substr(i * 2, 2), 16); return u; }
  function b64(u) { var s = ''; for (var i = 0; i < u.length; i++) s += String.fromCharCode(u[i]); return btoa(s); }
  function unb64(s) { var b = atob(s), u = new Uint8Array(b.length); for (var i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return u; }
  function rand(n) { var u = new Uint8Array(n); root.crypto.getRandomValues(u); return u; }

  // Kopflaenge (unverschluesselt, AAD) wie FrameCryptor: Opus 1, VP8 Schluesselbild 10, sonst 3; -1 = verwerfen
  function headerLen(kind, type) {
    if (kind === 'audio') return 1;
    if (type === 'key') return 10;
    if (type === 'delta') return 3;
    return -1;
  }
  function codecOk(kind, mime) {
    if (!mime) return true;                              // aeltere Browser melden den Codec nicht: Knoten erzwingt VP8/Opus
    mime = String(mime).toLowerCase();
    return kind === 'audio' ? mime === 'audio/opus' : mime === 'video/vp8';
  }

  // --- Ableitungen --------------------------------------------------------------------------------------------------
  // FK = HKDF-SHA256(K, salt "saga-horn/v1/frame", info 128 x 00, 16 B)  (= FrameCryptor KeyProvider im HKDF-Modus)
  // K' = HKDF-SHA256(K, salt "saga-horn-ratchet", info leer, 32 B)       (Saga-Ratsche, NICHT die FrameCryptor-Ratsche)
  async function frameKeyRaw(K) {
    var base = await subtle.importKey('raw', K, 'HKDF', false, ['deriveBits']);
    return new Uint8Array(await subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt: FRAME_SALT, info: INFO0 }, base, 128));
  }
  async function frameKey(K) {
    var raw = await frameKeyRaw(K);
    return { raw: raw, ck: await subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']) };
  }
  async function ratchet(K) {
    var base = await subtle.importKey('raw', K, 'HKDF', false, ['deriveBits']);
    return new Uint8Array(await subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt: RATCHET_SALT, info: EMPTY }, base, 256));
  }

  // --- Rahmen ---------------------------------------------------------------------------------------------------------
  // IV = SSRC (u32 BE) || RTP-Zeitstempel (u32 BE) || Zaehler (u32 BE) - Zaehler je (Schluessel, SSRC) ab 0
  async function encryptFrame(ck, data, h, kid, ssrc, ts, ctr) {
    var plen = data.length - h, out = new Uint8Array(h + plen + C.TAG + C.TRAILER), ivo = h + plen + C.TAG;
    var dv = new DataView(out.buffer);
    dv.setUint32(ivo, ssrc >>> 0); dv.setUint32(ivo + 4, ts >>> 0); dv.setUint32(ivo + 8, ctr >>> 0);
    out[ivo + 12] = C.IV; out[ivo + 13] = kid;
    out.set(data.subarray(0, h), 0);
    var ct = await subtle.encrypt({ name: 'AES-GCM', iv: out.subarray(ivo, ivo + C.IV), additionalData: data.subarray(0, h), tagLength: 128 },
                                  ck, data.subarray(h));
    out.set(new Uint8Array(ct), h);                      // einzige Kopie des Chiffrats (WebCrypto schreibt nicht in fremde Puffer)
    return out;
  }
  // -> {kid, ssrc, ts, ivo} oder Grund
  function parseFrame(data, h) {
    if (h < 0) return 'codec';
    if (data.length < h + C.TAG + C.TRAILER) return 'short';
    if (data[data.length - 2] !== C.IV) return 'format';
    var kid = data[data.length - 1];
    if (kid >= C.RING) return 'format';
    var ivo = data.length - C.TRAILER, dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
    return { kid: kid, ivo: ivo, ssrc: dv.getUint32(ivo), ts: dv.getUint32(ivo + 4) };
  }
  async function decryptFrame(ck, data, h, p) {
    var pt;
    try {
      pt = await subtle.decrypt({ name: 'AES-GCM', iv: data.subarray(p.ivo, p.ivo + C.IV), additionalData: data.subarray(0, h), tagLength: 128 },
                                ck, data.subarray(h, p.ivo));
    } catch (e) { return null; }
    var out = new Uint8Array(h + pt.byteLength);
    out.set(data.subarray(0, h), 0); out.set(new Uint8Array(pt), h);
    return out;
  }

  // --- Wiederholungsschutz: die 128 zuletzt angenommenen RTP-Zeitstempel je (Sender, Schluessel, SSRC aus der IV) ----
  // Serielle Arithmetik mod 2^32: d = (ts - max) mod 2^32, 1 <= d < 2^31 = neuer. Pruefen VOR, Merken NACH dem Entschluesseln.
  function ReplayWindow(size) { this.size = size || C.WINDOW; this.vals = []; this.max = null; }
  ReplayWindow.prototype.rel = function (ts) {
    var d = (ts - this.max) >>> 0;
    if (d === 0) return 0;
    return d < 0x80000000 ? d : -(0x100000000 - d);
  };
  ReplayWindow.prototype.check = function (ts) {
    ts >>>= 0;
    if (this.max === null) return true;
    var r = this.rel(ts);
    if (r > 0) return true;
    if (r === 0 || this.vals.indexOf(ts) >= 0) return false;
    if (this.vals.length < this.size) return true;
    var oldest = 0;
    for (var i = 0; i < this.vals.length; i++) { var a = (this.max - this.vals[i]) >>> 0; if (a > oldest) oldest = a; }
    return -r < oldest;
  };
  ReplayWindow.prototype.accept = function (ts) {
    ts >>>= 0;
    if (this.max === null || this.rel(ts) > 0) this.max = ts;
    this.vals.push(ts);
    if (this.vals.length > this.size) this.vals.shift();
  };

  // --- Runensiegel: 6 Runen (aelteres Futhark) aus SHA-256(call_id || sortierte, eindeutige Geraeteschluessel) ------
  var RUNES = ['ᚠ', 'ᚢ', 'ᚦ', 'ᚨ', 'ᚱ', 'ᚲ', 'ᚷ', 'ᚹ', 'ᚺ', 'ᚾ', 'ᛁ', 'ᛃ', 'ᛇ', 'ᛈ', 'ᛉ', 'ᛊ', 'ᛏ', 'ᛒ', 'ᛖ', 'ᛗ', 'ᛚ', 'ᛜ', 'ᛞ', 'ᛟ'];
  var RUNE_NAMES = ['fehu', 'uruz', 'thurisaz', 'ansuz', 'raidho', 'kaunan', 'gebo', 'wunjo', 'hagalaz', 'naudiz', 'isa', 'jera', 'eihwaz',
                    'perthro', 'algiz', 'sowilo', 'tiwaz', 'berkanan', 'ehwaz', 'mannaz', 'laguz', 'ingwaz', 'dagaz', 'othala'];
  function cmp(a, b) { for (var i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i]; return a.length - b.length; }
  async function runes(callId, pks) {
    var list = pks.map(function (p) { return typeof p === 'string' ? unb64(p) : p; }).sort(cmp);
    list = list.filter(function (p, i) { return i === 0 || cmp(p, list[i - 1]) !== 0; });
    var head = enc.encode(callId), msg = new Uint8Array(head.length + 32 * list.length);
    msg.set(head, 0);
    list.forEach(function (p, i) { if (p.length !== 32) throw new Error('Geraeteschluessel != 32 Byte'); msg.set(p, head.length + 32 * i); });
    var hsh = new Uint8Array(await subtle.digest('SHA-256', msg));
    var v = 0, idx = [];
    for (var i = 0; i < 5; i++) v = v * 256 + hsh[i];  // 40 Bit, big-endian (exakt in Double)
    for (var j = 0; j < 6; j++) { idx.push(v % 24); v = Math.floor(v / 24); }
    return { hash: hex(hsh), idx: idx, runes: idx.map(function (x) { return RUNES[x]; }).join(''),
             names: idx.map(function (x) { return RUNE_NAMES[x]; }) };
  }

  // --- Signale fkey / fkey-ack (Body im versiegelten saga-call-Signal) ---------------------------------------------
  var SID = /^[0-9a-f]{16}$/, KEY64 = /^[A-Za-z0-9+/]{43}=$/;          // 32 Byte, Standard-Base64 mit Padding
  function fkeyBody(o) { return JSON.stringify({ t: 'fkey', n: o.n, kid: o.n % C.RING, key: b64(o.key), s: o.s }); }
  function fkeyAckBody(n, s, ok) { return JSON.stringify({ t: 'fkey-ack', n: n, s: s, ok: !!ok }); }
  // -> {n, key, s} oder null (ungueltig: ignorieren)
  function parseFkey(b) {
    if (!b || b.t !== 'fkey' || typeof b.n !== 'number' || b.n % 1 || b.n < 0 || b.n > C.MAX_N || b.kid !== b.n % C.RING) return null;
    if (typeof b.s !== 'string' || !SID.test(b.s) || typeof b.key !== 'string' || !KEY64.test(b.key)) return null;
    var k;
    try { k = unb64(b.key); } catch (e) { return null; }
    return k.length === 32 ? { n: b.n, key: k, s: b.s } : null;
  }
  function parseFkeyAck(b) {
    if (!b || b.t !== 'fkey-ack' || typeof b.n !== 'number' || b.n % 1 || b.n < -1 || b.n > C.MAX_N || typeof b.s !== 'string' || !SID.test(b.s)) return null;
    return { n: b.n, s: b.s, ok: b.ok === true };
  }

  // --- Schluesselplan eines Geraets (eigener Sendeschluessel + Schluessel der anderen Sender) -------------------------
  // now: Millisekunden (fuer Tests einspeisbar). opts.onNeed(from, n): Sender liefert seit NEED_MS nur Unlesbares.
  function Horn(opts) {
    this.o = opts || {};
    this.s = hex(rand(8));                               // Sitzung dieses Senders (neu bei jedem Start des Workers)
    this.cur = null; this.pend = null;                   // {n, K, fk, ctr: Map ssrc->Zaehler}; pend.at = Umschaltzeit
    this.peers = new Map();                              // from -> {s, keys: Map n->{K, fk, until, win}, newest, active, cand, ...}
  }
  Horn.prototype._mk = async function (n, K) { var f = await frameKey(K); return { n: n, K: K, fk: f, ctr: new Map() }; };
  Horn.prototype._newest = function () { return this.pend || this.cur; };
  Horn.prototype.tick = function (now) {
    if (this.pend && now >= this.pend.at) { this.cur = this.pend; this.pend = null; }
  };
  // mode: start (Zufall, sofort) | ratchet (Beitritt: neuester Schluessel geratscht, sofort bzw. Umschaltzeit bleibt) |
  //       rekey (Verlassen: Zufall, Umschalten nach SWITCH_MS). -> {n, kid, key, s} zum Versiegeln (an wen: Aufrufer)
  Horn.prototype.own = async function (mode, now, K) {
    this.tick(now);
    var top = this._newest(), k;
    if (mode === 'start' || !top) {
      k = await this._mk(top ? top.n + 1 : 0, K || rand(32)); this.cur = k; this.pend = null;
    } else if (mode === 'ratchet') {
      k = await this._mk(top.n + 1, await ratchet(top.K));
      if (this.pend) { k.at = this.pend.at; this.pend = k; } else this.cur = k;
    } else if (mode === 'rekey') {
      k = await this._mk(top.n + 1, K || rand(32)); k.at = now + C.SWITCH_MS; this.pend = k;
    } else throw new Error('mode');
    return { n: k.n, kid: k.n % C.RING, key: k.K, s: this.s };
  };
  Horn.prototype.switchNow = function () { if (this.pend) { this.cur = this.pend; this.pend = null; } };
  // fuer Nachzuegler (fkey-ack ok:false): nur der NEUESTE Schluessel (der aktive kann aelter als der Beitritt sein)
  Horn.prototype.ownKeys = function () {
    var k = this._newest();
    return k ? [{ n: k.n, kid: k.n % C.RING, key: k.K, s: this.s }] : [];
  };

  Horn.prototype.encrypt = async function (data, kind, type, ssrc, ts, now, mime) {
    this.tick(now);
    var k = this.cur, h = headerLen(kind, type);
    if (!k) return 'nokey';
    if (h < 0 || !codecOk(kind, mime)) return 'codec';
    if (data.length < h || data.length === 0) return 'short';
    var c = k.ctr.get(ssrc) || 0;
    if (c > 0xffffffff) return 'nokey';                   // nie erreicht (2^32 Rahmen je Schluessel und SSRC)
    k.ctr.set(ssrc, c + 1);
    return encryptFrame(k.fk.ck, data, h, k.n % C.RING, ssrc, ts, c);
  };

  Horn.prototype._peer = function (from) {
    var p = this.peers.get(from);
    if (!p) { p = { s: null, keys: new Map(), newest: -1, active: -1, cand: new Map(), ok: 0, bad: 0, needAt: 0 }; this.peers.set(from, p); }
    return p;
  };
  // fkey eines anderen Senders einsetzen -> true (eingesetzt) | false (veraltet/ungueltig)
  Horn.prototype.peerKey = async function (from, n, K, s, now) {
    var p = this._peer(from);
    if (p.s !== s) {                                      // neue Sitzung des Senders (Neustart): alles von vorn
      p.s = s; p.keys = new Map(); p.newest = -1; p.active = -1; p.cand = new Map();
    }
    if (n < p.newest - 1) return false;
    var e = { K: K, fk: await frameKey(K), until: Infinity, win: new Map() };
    var old = p.keys.get(n);
    if (old && hex(old.K) === hex(K)) return true;       // doppelt zugestellt
    p.keys.forEach(function (x, m) { if (m !== n && m % C.RING === n % C.RING) p.keys.delete(m); });
    p.keys.set(n, e); p.cand.delete(n);
    if (n > p.newest) p.newest = n;
    this._trim(p, now);
    return true;
  };
  Horn.prototype._trim = function (p, now) {
    p.keys.forEach(function (x, m) { if (m < p.newest - C.KEEP + 1 || x.until <= now) p.keys.delete(m); });
  };
  Horn.prototype.peerGone = function (from) { this.peers.delete(from); };

  // -> Uint8Array (Klartext-Rahmen) oder Grund: nokey | auth | replay | short | format | codec
  Horn.prototype.decrypt = async function (from, data, kind, type, now, mime) {
    var p = this.peers.get(from), h = headerLen(kind, type);
    if (!codecOk(kind, mime)) return 'codec';
    var f = parseFrame(data, h);
    if (typeof f === 'string') return f;
    if (!p || p.newest < 0) return this._fail(from, p, 'nokey', now);
    this._trim(p, now);
    var k = null, kn = -1, cand = false;
    p.keys.forEach(function (x, m) { if (m % C.RING === f.kid) { k = x; kn = m; } });
    if (!k) {                                             // Ratsche erst beim ersten Rahmen mit neuerem kid (Beitritt)
      var top = p.keys.get(p.newest), j = (f.kid - (p.newest % C.RING) + C.RING) % C.RING;
      if (!top || j < 1 || j > C.AHEAD) return this._fail(from, p, 'nokey', now);
      kn = p.newest + j; k = p.cand.get(kn);
      if (!k) {
        var K = top.K;
        for (var i = 0; i < j; i++) K = await ratchet(K);
        k = { K: K, fk: await frameKey(K), until: Infinity, win: new Map() };
        p.cand.set(kn, k);
      }
      cand = true;
    }
    var w = k.win.get(f.ssrc);
    if (w && !w.check(f.ts)) return 'replay';
    var out = await decryptFrame(k.fk.ck, data, h, f);
    if (!out) return this._fail(from, p, 'auth', now);
    if (!w) { w = new ReplayWindow(); k.win.set(f.ssrc, w); }
    if (!w.check(f.ts)) return 'replay';                  // gleichzeitige Rahmen (await) - erneut pruefen
    w.accept(f.ts);
    if (cand && !p.keys.has(kn)) { p.keys.set(kn, k); p.cand.delete(kn); if (kn > p.newest) p.newest = kn; }
    if (kn > p.active) {                                  // erster Rahmen mit neuerem Schluessel: aeltere laufen in GRACE_MS ab
      p.active = kn;
      p.keys.forEach(function (x, m) { if (m < kn && x.until === Infinity) x.until = now + C.GRACE_MS; });
    }
    p.ok = now; p.bad = 0;
    return out;
  };
  Horn.prototype._fail = function (from, p, why, now) {
    if (p) {
      if (!p.bad) p.bad = now;
      var since = Math.max(p.ok || 0, p.bad);
      if (now - since >= C.NEED_MS && now - p.needAt >= C.NEED_EVERY && this.o.onNeed) { p.needAt = now; this.o.onNeed(from, p.newest, p.s); }
    }
    return why;
  };

  // --- Selbsttest gegen tests/vectors/horn_e2e.json (Node, Worker im Browser) ---------------------------------------
  async function selftest(V) {
    var fails = [], n = 0;
    function chk(name, cond, info) { n++; if (!cond) fails.push(name + (info !== undefined ? ' ' + JSON.stringify(info) : '')); }
    var keys = {};
    for (var i = 0; i < V.keys.length; i++) {
      var x = V.keys[i], K = unhex(x.K);
      if (i > 0) chk('ratsche n=' + x.n, hex(await ratchet(unhex(V.keys[i - 1].K))) === x.K);
      var fk = await frameKey(K);
      chk('fk n=' + x.n, hex(fk.raw) === x.fk);
      keys[x.n] = fk;
    }
    for (var j = 0; j < V.frames.length; j++) {
      var fr = V.frames[j], h = headerLen(fr.kind, fr.type), data = unhex(fr.plain), k = keys[fr.key_n];
      chk(fr.name + ': Kopflaenge', h === fr.h);
      if (fr.iv_mode !== 'fc') {
        var out = await encryptFrame(k.ck, data, h, fr.key_n % C.RING, fr.ssrc, fr.ts, fr.ctr);
        chk(fr.name + ': verschluesseln', hex(out) === fr.out);
      }
      var p = parseFrame(unhex(fr.out), h);
      var pt = typeof p === 'string' ? null : await decryptFrame(k.ck, unhex(fr.out), h, p);
      chk(fr.name + ': entschluesseln', pt && hex(pt) === fr.plain);
    }
    for (var r = 0; r < V.cases.length; r++) {             // Empfaenger-Faelle (Ablehnung, Wiederholung, Ratsche, Wechsel)
      var cs = V.cases[r], hn = new Horn({}), t = 0;
      for (var q = 0; q < cs.have.length; q++) await hn.peerKey('s', cs.have[q], unhex(V.keys[cs.have[q]].K), 'aaaaaaaaaaaaaaaa', 0);
      for (var st = 0; st < cs.steps.length; st++) {
        var sp = cs.steps[st];
        t = sp.at !== undefined ? sp.at : t;
        var res = await hn.decrypt('s', unhex(sp.data), sp.kind, sp.type, t);
        var got = typeof res === 'string' ? res : (hex(res) === sp.plain ? 'ok' : 'falsch');
        chk(cs.name + ' #' + st, got === sp.expect, got);
      }
    }
    var wv = new ReplayWindow(V.window.size);
    V.window.steps.forEach(function (s, i) {
      var okk = wv.check(s.ts);
      if (okk) wv.accept(s.ts);
      chk('fenster #' + i, okk === s.ok, s.ts);
    });
    for (var u = 0; u < V.runes.length; u++) {
      var rr = V.runes[u], got2 = await runes(rr.call_id, rr.devices.map(unhex));
      chk('runen ' + rr.name, got2.hash === rr.hash && got2.runes === rr.runes && got2.idx.join() === rr.idx.join(), got2);
    }
    var fb = JSON.parse(V.fkey.body), pf = parseFkey(fb);
    chk('fkey parsen', pf && pf.n === V.fkey.n && hex(pf.key) === V.fkey.key_hex);
    V.fkey.invalid.forEach(function (b, i) { chk('fkey ungueltig #' + i, parseFkey(b) === null); });
    return { ok: fails.length === 0, n: n, fails: fails };
  }

  // Messung (Diagnose): je Rahmen nacheinander (Wartezeit inkl. WebCrypto-Thread) und 32 gleichzeitig (Durchsatz)
  async function bench(size, n) {
    var fk = await frameKey(rand(32)), data = rand(size), t0 = performance.now(), i, out;
    for (i = 0; i < n; i++) out = await encryptFrame(fk.ck, data, 3, 0, 1, i, i);
    var t1 = performance.now(), p = parseFrame(out, 3);
    for (i = 0; i < n; i++) await decryptFrame(fk.ck, out, 3, p);
    var t2 = performance.now(), jobs = [];
    for (i = 0; i < n; i++) jobs.push(encryptFrame(fk.ck, data, 3, 0, 2, i, i));
    await Promise.all(jobs);
    var t3 = performance.now();
    return { size: size, n: n, enc_ms: (t1 - t0) / n, dec_ms: (t2 - t1) / n, enc_parallel_ms: (t3 - t2) / n };
  }

  var core = { C: C, headerLen: headerLen, codecOk: codecOk, frameKey: frameKey, frameKeyRaw: frameKeyRaw, ratchet: ratchet,
               encryptFrame: encryptFrame, parseFrame: parseFrame, decryptFrame: decryptFrame, ReplayWindow: ReplayWindow, runes: runes,
               RUNES: RUNES, RUNE_NAMES: RUNE_NAMES, fkeyBody: fkeyBody, fkeyAckBody: fkeyAckBody, parseFkey: parseFkey,
               parseFkeyAck: parseFkeyAck, Horn: Horn, selftest: selftest, bench: bench, hex: hex, unhex: unhex, b64: b64, unb64: unb64 };

  // --- Rolle 1: Node -------------------------------------------------------------------------------------------------
  if (typeof module === 'object' && module.exports) { module.exports = core; return; }

  // --- Rolle 2: Worker -----------------------------------------------------------------------------------------------
  var inWorker = typeof WorkerGlobalScope !== 'undefined' && root instanceof WorkerGlobalScope;
  if (inWorker) {
    var H = new Horn({ onNeed: function (from, n, s) { root.postMessage({ ev: 'need', from: from, n: n, s: s }); } });
    var fresh = function () { return { enc: {}, dec: {}, drop: {}, kid: { send: -1, recv: {} } }; }, S = fresh();
    var clock = function () { return performance.now(); };
    var tally = function (bag, k, ms, bytes) {
      var x = bag[k] || (bag[k] = { n: 0, ms: 0, max: 0, bytes: 0 });
      x.n++; x.ms += ms; x.bytes += bytes; if (ms > x.max) x.max = ms;
    };
    var drop = function (why, kind) { S.drop[why] = (S.drop[why] || 0) + 1; if (kind) S.drop[kind + ':' + why] = (S.drop[kind + ':' + why] || 0) + 1; };
    // 2.5 P3: Empfaenger am Knoten (from '*'): Absender je Rahmen aus der CSRC (getMetadata().contributingSources) ueber die
    // Zuordnung CSRC -> Geraet, die das Fenster aus der Belegung der Plaetze meldet (op "csrc"). Unbekannt: verwerfen.
    // 2.5 P5: eine Zuordnung je Knoten-Verbindung (o.map; CSRCs vergibt jeder Knoten selbst - beim Wechsel zwischen Knoten
    // laufen kurz zwei Verbindungen nebeneinander)
    var MAPS = { '': {} }, mapWait = [];
    // Absender unbekannt: kurz (hoechstens 150 ms) auf die naechste Zuordnung warten - beim Wechsel zwischen Knoten kann
    // der erste Rahmen eines Geraets knapp vor dessen Meldung im Ereigniskanal ankommen
    var waitMap = function (key, csrc) {
      return new Promise(function (ok) {
        var w = { key: key, csrc: csrc, ok: ok };
        mapWait.push(w);
        setTimeout(function () { var i = mapWait.indexOf(w); if (i >= 0) mapWait.splice(i, 1); ok((MAPS[key] || {})[csrc]); }, 150);
      });
    };
    // 2.5 P5: Bild-Empfaenger ohne gueltiges Schluesselbild (erste Rahmen ohne Schluessel bzw. Absender verworfen):
    // Schluesselbild anfordern, sobald wieder Rahmen aufgehen (sonst bleibt das Bild schwarz, bis der Sender von sich aus
    // eines schickt - der Dekodierer sieht die verworfenen Rahmen nicht und fordert selbst nichts an). Hoechstens 1x je s.
    var pipe = function (readable, writable, o, tr) {
      var send = o.side === 'send', needKey = true, keyAt = 0, lastFrom = null;
      var wantKey = function () {
        var now = Date.now();
        if (!tr || typeof tr.sendKeyFrameRequest !== 'function' || now - keyAt < 1000) return;
        keyAt = now; S.keyreq = (S.keyreq || 0) + 1;
        try { var p = tr.sendKeyFrameRequest(); if (p && p.catch) p.catch(function () {}); } catch (e) { /* aelterer Browser */ }
      };
      readable.pipeThrough(new TransformStream({
        transform: async function (f, c) {
          var t0 = clock(), md = f.getMetadata ? f.getMetadata() : {};
          var kind = o.kind || (f.type === undefined ? 'audio' : 'video'), type = f.type;
          var data = new Uint8Array(f.data), r, from = o.from;
          if (!send && from === '*') {
            var csrc = ((md.contributingSources || [])[0]) >>> 0;
            from = (MAPS[o.map || ''] || {})[csrc];
            if (!from && csrc && mapWait.length < 64) { from = await waitMap(o.map || '', csrc); if (from) S.late = (S.late || 0) + 1; }
            if (!from) { drop('nofrom', kind); if (kind === 'video') needKey = true; return; }
            // 2.5 P5: tatsaechlicher Absender je Bild-Empfaenger melden - der Knoten wechselt die Quelle eines Platzes erst am
            // naechsten Schluesselbild, die Belegung nennt aber schon die neue; das Fenster ordnet die Kachel danach zu
            if (kind === 'video' && o.rid && from !== lastFrom) { lastFrom = from; root.postMessage({ ev: 'vown', rid: o.rid, from: from }); }
          }
          try {
            if (send) {
              var ts = md.rtpTimestamp !== undefined ? md.rtpTimestamp : f.timestamp;
              r = await H.encrypt(data, kind, type, md.synchronizationSource >>> 0, ts >>> 0, Date.now(), md.mimeType);
            } else r = await H.decrypt(from, data, kind, type, Date.now(), md.mimeType);
          } catch (e) { r = 'error'; }
          if (typeof r === 'string') { drop(r, kind); if (!send && kind === 'video') needKey = true; return; }  // keine Ausgabe
          if (!send && kind === 'video') { if (type === 'key') needKey = false; else if (needKey) wantKey(); }
          f.data = r.buffer;
          c.enqueue(f);
          tally(send ? S.enc : S.dec, kind + (kind === 'video' ? ':' + type : ''), clock() - t0, data.length);
          if (send) S.kid.send = r[r.length - 1]; else S.kid.recv[from] = data[data.length - 1];
        }
      })).pipeTo(writable).catch(function () {});
    };
    root.onrtctransform = function (ev) { var t = ev.transformer; pipe(t.readable, t.writable, t.options || {}, t); };
    root.onmessage = async function (m) {
      var d = m.data || {}, out = { id: d.id };
      try {
        if (d.op === 'stream') pipe(d.readable, d.writable, d.o || {});
        else if (d.op === 'own') out.key = await H.own(d.mode, Date.now(), d.key ? new Uint8Array(d.key) : undefined);
        else if (d.op === 'own-keys') out.keys = H.ownKeys();
        else if (d.op === 'switch') H.switchNow();
        else if (d.op === 'peer') out.ok = await H.peerKey(d.from, d.n, new Uint8Array(d.key), d.s, Date.now());
        else if (d.op === 'gone') H.peerGone(d.from);
        else if (d.op === 'csrc') {
          var mm = MAPS[d.key || ''] = {}; Object.keys(d.map || {}).forEach(function (k) { mm[k >>> 0] = String(d.map[k]); });
          mapWait = mapWait.filter(function (w) { if (w.key === (d.key || '') && mm[w.csrc]) { w.ok(mm[w.csrc]); return false; } return true; });
        }
        else if (d.op === 'csrc-drop') { if (d.key) delete MAPS[d.key]; }
        else if (d.op === 'stats') { out.stats = S; if (d.reset) S = fresh(); }
        else if (d.op === 'selftest') out.result = await selftest(d.vectors);
        else if (d.op === 'runes') out.runes = await runes(d.call_id, d.devices);
        else if (d.op === 'bench') out.bench = await bench(d.size || 6000, d.n || 500);
      } catch (e) { out.error = String(e && e.message || e); }
      if (d.id) root.postMessage(out);
    };
    return;
  }

  // --- Rolle 3: Fenster ----------------------------------------------------------------------------------------------
  function supported() {
    if (typeof RTCRtpScriptTransform === 'function') return 'script';
    if (typeof RTCRtpSender !== 'undefined' && RTCRtpSender.prototype.createEncodedStreams) return 'streams';
    return null;
  }
  // Rueckfall "streams": die PeerConnection braucht {encodedInsertableStreams: true}.
  function create(opt) {
    opt = opt || {};
    var mode = opt.mode || supported();
    if (!mode) throw new Error('frame-e2e: Browser ohne Encoded Transform - keine Medien senden');
    var w = new Worker(opt.url || '/static/frame-e2e.js'), seq = 0, waits = {}, subs = {};
    w.onmessage = function (m) {
      var d = m.data || {};
      if (d.id && waits[d.id]) { var cb = waits[d.id]; delete waits[d.id]; if (d.error) cb[1](new Error(d.error)); else cb[0](d); }
      else if (d.ev) (subs[d.ev] || []).forEach(function (fn) { fn(d); });
    };
    function call(op, args) {
      return new Promise(function (ok, bad) { var id = ++seq; waits[id] = [ok, bad]; w.postMessage(Object.assign({ op: op, id: id }, args || {})); });
    }
    function attach(x, o) {
      if (mode === 'script') x.transform = new RTCRtpScriptTransform(w, o);
      else { var s = x.createEncodedStreams(); w.postMessage({ op: 'stream', o: o, readable: s.readable, writable: s.writable }, [s.readable, s.writable]); }
    }
    return {
      mode: mode, worker: w,
      sender: function (snd, kind) { attach(snd, { side: 'send', kind: kind }); },
      receiver: function (rcv, kind, from, map, rid) { attach(rcv, { side: 'recv', kind: kind, from: from, map: map || '', rid: rid || 0 }); },
      own: function (m, key) { return call('own', { mode: m, key: key }).then(function (d) { return d.key; }); },
      ownKeys: function () { return call('own-keys').then(function (d) { return d.keys; }); },
      switchNow: function () { return call('switch'); },
      peer: function (from, n, key, s) { return call('peer', { from: from, n: n, key: key, s: s }).then(function (d) { return d.ok; }); },
      gone: function (from) { return call('gone', { from: from }); },
      csrcMap: function (map, key) { return call('csrc', { map: map, key: key || '' }); },
      csrcDrop: function (key) { return call('csrc-drop', { key: key }); },
      stats: function (reset) { return call('stats', { reset: !!reset }).then(function (d) { return d.stats; }); },
      selftest: function (v) { return call('selftest', { vectors: v }).then(function (d) { return d.result; }); },
      runes: function (callId, devices) { return call('runes', { call_id: callId, devices: devices }).then(function (d) { return d.runes; }); },
      on: function (ev, fn) { (subs[ev] = subs[ev] || []).push(fn); },
      close: function () { w.terminate(); }
    };
  }
  root.SagaFrameE2E = Object.assign({ supported: supported, create: create }, core);
})(typeof self !== 'undefined' ? self : globalThis);
