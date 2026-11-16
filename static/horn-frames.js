// Saga 2.5 Gjallarhorn, Browser-Weg B2 "Schleifen-PeerConnection + WebSocket" (Vertrag vertrag-a3.md S12, docs/APP-API.md
// "Rahmen-Weg saga-frames.1"). Hinter einem TLS-beendenden Proxy (Nginx Proxy Manager) erreicht der Browser den Knoten nur
// ueber HTTPS/WSS auf der Saga-Adresse. Er kodiert und dekodiert weiter mit seiner WebRTC-Maschine, schickt die kodierten,
// E2E-versiegelten Rahmen aber ueber zwei WebSockets (Ton, Bild) an /api/sfu/ws (Unterprotokoll saga-frames.1):
//   - Senden: eine lokale Schleife pcX -> pcY im selben Tab. pcX traegt die echten Spuren (Ton, Bild l, Bild m, Bildschirm;
//     je Schicht ein Transceiver statt Simulcast). Der Sende-Transform "tap" versiegelt (saga-horn/v1) und schickt den Rahmen
//     ueber den WebSocket - an pcY geht nichts (Firefox: doch, damit der Encoder Rueckmeldung bekommt; pcY verwirft es).
//   - Empfangen: pcX sendet je Empfangsplatz eine Attrappe (16x16-Canvas bzw. stiller Ton) an pcY. Der Transform "inject"
//     ersetzt ihre Rahmen durch die echten, im Worker geoeffneten Rahmen vom WebSocket. pcY spielt sie wie einen normalen
//     Anruf ab: Jitter-Puffer, NetEq, Decoder und Echo-Unterdrueckung laufen ueber den echten WebRTC-Empfang.
// Eine Datei, zwei Rollen (wie frame-e2e.js): im Frame-E2E-Worker per importScripts (Schluessel bleiben dort, E2E "Variante
// W": Oeffnen vor dem Einspeisen) und im Fenster (window.SagaHornFrames.create: Schleife, Attrappen, WebSockets steuern).
(function (root) {
  'use strict';
  var HDR = 16, MSG_ACK = 0x01, MSG_FB = 0x02, MSG_FRAME = 0x10, FL_KEY = 1, FL_SYNC = 2;
  var TRACK = { audio: 0, l: 1, m: 2, screen: 3 };
  var K = {
    AQ_START: 1, AQ_MAX: 3,          // Ton-Warteschlange vor der Attrappe: Ziel 1 Rahmen, hoechstens 3 (b2-vorab: sonst +100-300 ms)
    VQ_MAX: 30,                      // Bild-Warteschlange je Platz (danach nur der neueste)
    BUF_TID: 256 << 10,              // Sendepuffer: darueber fallen nicht referenzierte Rahmen (hoechste Zeitschicht) weg
    BUF_ALL: 1 << 20,                // ... darueber alle Bildrahmen bis zum naechsten Schluesselbild
    UP_Q_HIGH: 60, UP_Q_LOW: 25,     // Aufwaerts-Regelung: Verzoegerungszunahme laut Knoten (ms)
    UP_BUF_HIGH: 64 << 10, UP_BUF_LOW: 16 << 10,
    UP_DOWN: 0.85, UP_HOLD: 2000, UP_STEP: 200, UP_INC: 1.06,
    KEY_EVERY: 1000, KF_MIN: 300, ST_EVERY: 1000
  };
  function nowMs() { return Math.floor(performance.timeOrigin + performance.now()) >>> 0; }   // Wanduhr (ms, mod 2^32)
  // Dauer eines Opus-Pakets aus dem TOC-Byte (bleibt bei der E2E-Versiegelung unverschluesselt) in 20-ms-Takten: ein
  // 40-ms-Paket (TCP-Wege senden ptime 40) belegt zwei Takte der Attrappe.
  function opusTicks(u8) {
    if (!u8.length) return 1;
    var toc = u8[0], c = toc >> 3, code = toc & 3;
    var ms = c < 12 ? [10, 20, 40, 60][c & 3] : c < 16 ? [10, 20][c & 1] : [2.5, 5, 10, 20][c & 3];
    if (code === 1 || code === 2) ms *= 2;
    else if (code === 3 && u8.length > 1) ms *= (u8[1] & 0x3f) || 1;
    return Math.max(1, Math.round(ms / 20));
  }
  var core = { K: K, HDR: HDR, TRACK: TRACK, opusTicks: opusTicks };

  // --- Rolle 1: Worker (im Frame-E2E-Worker, static/frame-e2e.js op "frames") -----------------------------------------
  var inWorker = typeof WorkerGlobalScope !== 'undefined' && root instanceof WorkerGlobalScope;
  if (inWorker) {
    var X = null;                    // {H, maps, waitMap, drop, post}
    var C = {};                      // Verbindung (conn.key) -> {ws: {audio, video}, inj: {audio: [], video: []}, taps: {}, up, need: {}}
    var WS = {};                     // id -> Leitung
    var level = 127;                 // eigener Pegel (-dBov, 127 = still) vom Fenster
    var cnt = {};
    var bump = function (k) { cnt[k] = (cnt[k] || 0) + 1; };
    var conn = function (key) {
      return C[key] || (C[key] = { ws: {}, inj: { audio: [], video: [] }, taps: {}, need: {}, kfAt: {},
                                    up: { max: 600, min: 150, target: 600, last: 0, sentB: 0, holdUntil: 0 } });
    };
    var post = function (m) { if (X) X.post(Object.assign({ ev: 'fr' }, m)); };

    // Schluesselbild fuer eine eigene Spur: Firefox/WebKit am Transformer, Chromium (fehlt) per active aus/an im Fenster
    var keyframe = function (key, track) {
      var c = C[key]; if (!c) return;
      var now = Date.now();
      if (now - (c.kfAt[track] || 0) < K.KF_MIN) return;
      c.kfAt[track] = now;
      var tp = c.taps[track];
      if (tp && typeof tp.generateKeyFrame === 'function') {
        tp.generateKeyFrame().then(function () { bump('genkey'); }, function () { post({ t: 'kf', key: key, track: track }); });
      } else post({ t: 'kf', key: key, track: track });
    };

    var open = function (d) {
      var c = conn(d.key), old = c.ws[d.role];
      var ws;
      try { ws = new WebSocket(d.url, ['saga-frames.1']); } catch (e) { post({ t: 'err', id: d.id, key: d.key, role: d.role, code: 'ws' }); return; }
      ws.binaryType = 'arraybuffer';
      var w = { id: d.id, key: d.key, role: d.role, ws: ws, ok: null, rx: 0, acked: 0, ackT: null, ackBytes: 8192, ackMs: 10,
                dMin: null, dMinAt: 0, qDown: 0, qUp: 0, rtt: null, tx: 0, fno: {}, t0: performance.now(), done: false, stT: null, keyAt: {} };
      WS[d.id] = w;
      if (!old || old.done) c.ws[d.role] = w;          // sonst erst mit ok (L3/Wiederverbinden: die alte Leitung traegt bis dahin)
      ws.onopen = function () { try { ws.send(JSON.stringify(d.join)); } catch (e) { /* zu */ } };
      ws.onclose = function (e) {
        if (w.done) return;
        w.done = true; clearTimeout(w.ackT); clearInterval(w.stT); delete WS[w.id];
        if (C[w.key] && C[w.key].ws[w.role] === w) delete C[w.key].ws[w.role];
        post({ t: 'closed', id: w.id, key: w.key, role: w.role, code: e.code, ok: !!w.ok });
      };
      ws.onmessage = function (e) {
        if (typeof e.data === 'string') { text(w, e.data); return; }
        binary(w, e.data);
      };
    };
    var closeW = function (w, code) {
      if (w.done) return;
      w.done = true; clearTimeout(w.ackT); clearInterval(w.stT); delete WS[w.id];
      if (C[w.key] && C[w.key].ws[w.role] === w) delete C[w.key].ws[w.role];
      try { w.ws.close(code || 1000); } catch (e) { /* zu */ }
    };
    var text = function (w, s) {
      var j; try { j = JSON.parse(s); } catch (e) { return; }
      if (j.t === 'ok' && !w.ok) {
        w.ok = j;
        var c = conn(w.key), prev = c.ws[w.role];
        c.ws[w.role] = w;                                // der Knoten hat das alte Bein (gleiche leg_ref) ersetzt
        if (prev && prev !== w) closeW(prev, 1000);
        if (j.ack_bytes > 0 && j.ack_bytes <= 1 << 20) w.ackBytes = j.ack_bytes;
        if (j.ack_ms > 0 && j.ack_ms <= 1000) w.ackMs = j.ack_ms;
        w.stT = setInterval(function () { stats(w); }, K.ST_EVERY);
        post({ t: 'ok', id: w.id, key: w.key, role: w.role, ok: j, ms: Math.round(performance.now() - w.t0) });
        // erste Rahmen jeder Bild-Spur sollen Schluesselbilder sein (alles davor ging nirgends hin)
        if (w.role === 'video') [1, 2, 3].forEach(function (tr) { conn(w.key).need[tr] = true; keyframe(w.key, tr); });
      } else if (j.t === 'err') post({ t: 'err', id: w.id, key: w.key, role: w.role, code: String(j.code || '').slice(0, 16) });
      else if (j.t === 'pli' && typeof j.track === 'number') { bump('pli'); keyframe(w.key, j.track); }
    };
    var stats = function (w) {
      post({ t: 'st', id: w.id, key: w.key, role: w.role, rtt: w.rtt, qUp: w.qUp, qDown: w.qDown, buf: w.ws.bufferedAmount, tx: w.tx, rx: w.rx,
             up: w.role === 'video' ? Math.round(conn(w.key).up.target) : null });
    };
    var ackNow = function (w) {
      clearTimeout(w.ackT); w.ackT = null;
      if (w.rx === w.acked || w.ws.readyState !== 1) return;
      w.acked = w.rx;
      var m = new ArrayBuffer(11), dv = new DataView(m);
      dv.setUint8(0, MSG_ACK);
      dv.setUint32(1, Math.floor(w.rx / 4294967296)); dv.setUint32(5, w.rx >>> 0);
      dv.setUint16(9, Math.min(65535, Math.max(0, w.qDown)));
      try { w.ws.send(m); } catch (e) { /* zu */ }
    };
    var binary = function (w, b) {
      if (!w.ok || b.byteLength < 1) return;
      var dv = new DataView(b), t = dv.getUint8(0);
      if (t === MSG_FB && b.byteLength >= 17) {
        var echo = dv.getUint32(9), hold = dv.getUint16(13);
        w.qUp = dv.getUint16(15);
        if (echo) { var r = ((nowMs() - echo) | 0) - hold; if (r >= 0 && r < 60000) w.rtt = r; }
        upControl(w);
        return;
      }
      if (t !== MSG_FRAME || b.byteLength <= HDR) return;
      w.rx += b.byteLength - 1;
      if (w.rx - w.acked >= w.ackBytes) ackNow(w);
      else if (!w.ackT) w.ackT = setTimeout(function () { ackNow(w); }, w.ackMs);
      var slot = dv.getUint8(1), fl = dv.getUint8(2), ts = dv.getUint32(4), csrc = dv.getUint32(8) >>> 0, sent = dv.getUint32(12);
      var d = (nowMs() - sent) | 0, pn = performance.now();
      if (w.dMin === null || d < w.dMin || pn - w.dMinAt > 10000) { w.dMin = d; w.dMinAt = pn; }
      w.qDown = Math.max(0, d - w.dMin);
      var S = conn(w.key).inj[w.role][slot];
      if (!S) { bump('noslot'); return; }
      openThenPush(w, S, slot, { data: new Uint8Array(b, HDR), key: (fl & FL_KEY) !== 0, ts: ts, csrc: csrc, at: pn });
    };
    // Variante W (b2-vorab): je Platz in Reihenfolge oeffnen; ohne Absender/Schluessel nichts einspeisen. Bild wartet danach
    // auf ein Schluesselbild (sonst dekodiert pcY Delta-Rahmen auf falscher Referenz) und fordert es beim Knoten an.
    var openThenPush = function (w, S, slot, f) {
      S.chain = (S.chain || Promise.resolve()).then(async function () {
        var maps = X.maps[w.key] || {}, from = maps[f.csrc];
        if (!from && f.csrc) { from = await X.waitMap(w.key, f.csrc); }
        var r = 'nofrom';
        if (from) {
          try { r = await X.H.decrypt(from, f.data, S.kind, S.kind === 'audio' ? undefined : (f.key ? 'key' : 'delta'), Date.now()); } catch (e) { r = 'error'; }
        }
        if (typeof r === 'string') {
          X.drop(r, S.kind);
          if (S.kind === 'video') { S.waitKey = true; wantKey(w, S, slot); }
          return;
        }
        if (S.kind === 'video') {
          if (f.key) S.waitKey = false;
          else if (S.waitKey) { bump('waitkey'); wantKey(w, S, slot); return; }
          if (S.rid && from !== S.from) { S.from = from; X.post({ ev: 'vown', rid: S.rid, from: from }); }
        }
        f.data = r;
        push(w, S, slot, f);
      });
    };
    var wantKey = function (w, S, slot) {
      var now = Date.now();
      if (now - (S.keyAt || 0) < K.KEY_EVERY) return;
      S.keyAt = now;
      var cur = conn(w.key).ws[w.role];
      if (cur && cur.ws.readyState === 1) { try { cur.ws.send(JSON.stringify({ t: 'key', slot: slot })); } catch (e) { /* zu */ } }
    };
    var push = function (w, S, slot, f) {
      if (S.kind === 'video') {
        S.q.push(f);
        if (S.q.length > K.VQ_MAX) { S.q.splice(0, S.q.length - 1); bump('vq-over'); }
        post({ t: 'dummy', key: w.key, slot: slot });
      } else S.q.push(f);
    };
    // Ton: die Attrappe tickt alle 20 ms; jeder Takt wird durch den naechsten echten Rahmen ersetzt. Leer -> Takt entfaellt
    // (NetEq verdeckt), zu lang -> aelteste verwerfen: nach dem Takt wartet hoechstens das Ziel (1 Rahmen; nach Unterlaeufen
    // bis 3, nach 8 s ruhig wieder 1). Gemessen: ein stehender Rahmen mehr kostete +40 ms Ton bei freier Leitung.
    var audioTick = function (S, fr, writer) {
      var q = S.q, now = performance.now();
      if (S.skip > 0) { S.skip--; return; }
      if (q.length) S.lastIn = now;
      while (q.length > S.max) { q.shift(); bump('a-trim'); }
      if (!q.length) {
        if (now - (S.lastIn || 0) < 1000) { bump('a-under'); if (now - S.under > 200) S.max = Math.min(S.max + 1, K.AQ_MAX); S.under = now; }
        return;
      }
      if (S.max > K.AQ_START && now - S.under > 8000) { S.max--; S.under = now - 4000; }
      var f = q.shift();
      fr.data = f.data.byteOffset || f.data.byteLength !== f.data.buffer.byteLength ? f.data.slice().buffer : f.data.buffer;
      S.skip = opusTicks(f.data) - 1;
      writer.write(fr);
      bump('inj-a');
    };

    // Aufwaerts-Regelung (Bild m): Verzoegerungszunahme laut Knoten und eigener Sendepuffer
    var upControl = function (w) {
      if (w.role !== 'video') return;
      var u = conn(w.key).up, now = performance.now();
      if (now - u.last < K.UP_STEP) return;
      var dt = (now - u.last) / 1000;
      u.last = now;
      var rate = u.sentAt ? (w.tx - u.sentB) * 8 / dt / 1000 : 0;
      u.sentB = w.tx; u.sentAt = now;
      var q = w.qUp, buf = w.ws.bufferedAmount, t = u.target;
      if (q > K.UP_Q_HIGH || buf > K.UP_BUF_HIGH) { t = Math.max(u.min, Math.min(t, rate || t) * K.UP_DOWN); u.holdUntil = now + K.UP_HOLD; }
      else if (q < K.UP_Q_LOW && buf < K.UP_BUF_LOW && now >= u.holdUntil) t = Math.min(u.max, t * K.UP_INC + 10);
      u.target = t;
      var last = u.posted || u.max;                   // gemeldet wird erst eine Aenderung > 8 % (bzw. Grenze erreicht)
      if (Math.round(t) !== Math.round(last) && (Math.abs(t - last) / last > 0.08 || t === u.max || t === u.min)) {
        u.posted = t; post({ t: 'rate', key: w.key, kbps: Math.round(t) });
      }
    };

    // Rahmen auf den WebSocket (aufwaerts)
    var sendFrame = function (key, role, track, data, fl, x, ts, top) {
      var c = C[key], w = c && c.ws[role];
      if (!w || !w.ok || w.ws.readyState !== 1) { bump('tx-nows'); if (role === 'video') c && (c.need[track] = true); return false; }
      var buf = w.ws.bufferedAmount;
      if (role === 'video') {
        var key_ = (fl & FL_KEY) !== 0;
        if (c.need[track] && !key_) { bump('tx-needkey'); keyframe(key, track); return false; }
        if (!key_ && buf > K.BUF_ALL) { c.need[track] = true; keyframe(key, track); bump('tx-drop'); return false; }
        if (!key_ && top > 0 && x === top && buf > K.BUF_TID) { bump('tx-tiddrop'); return false; }   // keine Referenz: sicher
        if (key_) c.need[track] = false;
      }
      var m = new ArrayBuffer(HDR + data.byteLength), dv = new DataView(m);
      var fno = w.fno[track] = ((w.fno[track] || 0) + 1) >>> 0;
      dv.setUint8(0, MSG_FRAME); dv.setUint8(1, track); dv.setUint8(2, fl); dv.setUint8(3, x);
      dv.setUint32(4, ts >>> 0); dv.setUint32(8, fno); dv.setUint32(12, nowMs());
      new Uint8Array(m, HDR).set(data);
      try { w.ws.send(m); } catch (e) { return false; }
      w.tx += m.byteLength;
      bump(role === 'audio' ? 'tx-a' : 'tx-v');
      return true;
    };

    var transform = function (t, o) {
      if (o.side === 'tap') { tap(t, o); return true; }
      if (o.side === 'inject') { inject(t, o); return true; }
      // Firefox (fwd): pcY dekodiert die eigenen Bild-Spuren - ohne Rahmen am Decoder forderte pcY dauernd Schluesselbilder an
      // (gemessen 2,5 je s beim Empfaenger am Knoten). Nur lokal (Schleife im Tab, DTLS-SRTP), nichts verlaesst den Rechner.
      if (o.side === 'sink') { t.readable.pipeThrough(new TransformStream({ transform: function (f, c) { bump('sink'); c.enqueue(f); } })).pipeTo(t.writable).catch(function () {}); return true; }
      return false;
    };
    var tap = function (t, o) {
      var c = conn(o.key), track = o.track, kind = track === 0 ? 'audio' : 'video', role = kind === 'audio' ? 'audio' : 'video';
      c.taps[track] = t;
      var fw = o.fwd ? t.writable.getWriter() : null, tids = new Map();
      t.readable.pipeTo(new WritableStream({ write: async function (fr) {
        var md = fr.getMetadata ? fr.getMetadata() : {}, type = fr.type, data = new Uint8Array(fr.data), r;
        // Firefox: Rahmen unversiegelt an die lokale Schleife (pcY dekodiert ihn; Kopie vor dem Versiegeln)
        if (fw) { data = data.slice(); fw.write(fr).catch(function () {}); }
        try {
          r = await X.H.encrypt(data, kind, kind === 'audio' ? undefined : type, md.synchronizationSource >>> 0,
                                (md.rtpTimestamp !== undefined ? md.rtpTimestamp : fr.timestamp) >>> 0, Date.now(), md.mimeType);
        } catch (e) { r = 'error'; }
        if (typeof r === 'string') { X.drop(r, kind); return; }            // nie Klartext senden
        var ts = (md.rtpTimestamp !== undefined ? md.rtpTimestamp : fr.timestamp) >>> 0;
        if (kind === 'audio') sendFrame(o.key, role, 0, r, 0, level, ts, 0);
        else {
          // Y-Bit (layer sync, RFC 7741): hoehere Zeitschicht, die nur von Basisrahmen abhaengt - ohne es verwirft der
          // Referenz-Finder der Empfaenger (libwebrtc) alle Rahmen mit TID > 0
          var key_ = type === 'key', tid = key_ ? 0 : (md.temporalIndex || 0) & 3;
          if (md.frameId !== undefined) { tids.set(md.frameId, tid); if (tids.size > 64) tids.delete(tids.keys().next().value); }
          var sync = tid > 0 && Array.isArray(md.dependencies) && md.dependencies.length > 0 && md.dependencies.every(function (x) { return tids.get(x) === 0; });
          sendFrame(o.key, role, track, r, (key_ ? FL_KEY : 0) | (sync ? FL_SYNC : 0), tid, ts, o.top || 0);
        }
        // Firefox: Rahmen zusaetzlich an pcY - die Sende-Schleife bekommt Rueckmeldung (TWCC), sonst bleibt die
        // Encoder-Zielrate auf der Startrate. pcY verwirft sie (sink, kein Dekodieren).

      } })).catch(function () {});
    };
    var inject = function (t, o) {
      var c = conn(o.key), writer = t.writable.getWriter(), list = c.inj[o.role];
      var S = list[o.slot] = { kind: o.kind, rid: o.rid || 0, q: [], max: K.AQ_START, under: 0, skip: 0, lastIn: 0, waitKey: true, n: 0 };
      t.readable.pipeTo(new WritableStream({ write: function (fr) {
        if (S.kind === 'audio') { audioTick(S, fr, writer); return; }
        S.n++;
        if (fr.type === 'key' && S.n > 1) {
          // Schluesselbild der Attrappe nach dem Start = pcY will eines: an den Knoten weiterreichen
          bump('keyreq');
          var w = c.ws.video; if (w && w.ws.readyState === 1) { try { w.ws.send(JSON.stringify({ t: 'key', slot: o.slot })); } catch (e) { /* zu */ } }
        }
        if (!S.q.length) { bump('underrun'); return; }
        var f = S.q.shift();
        fr.data = f.data.byteOffset || f.data.byteLength !== f.data.buffer.byteLength ? f.data.slice().buffer : f.data.buffer;
        writer.write(fr);
        bump('inj-v');
        // Nach einem Schub fasst captureStream schnelle requestFrame zusammen: weiter anfordern, bis die Warteschlange leer ist
        if (S.q.length) post({ t: 'dummy', key: o.key, slot: o.slot });
      } })).catch(function () {});
    };

    var msg = function (d) {
      if (d.a === 'open') open(d);
      else if (d.a === 'close') { var w = WS[d.id]; if (w) closeW(w, 1000); }
      else if (d.a === 'level') level = d.v & 0xff;
      else if (d.a === 'kill') {                       // nur Tests: Leitungen wie bei einem Netzabbruch verlieren
        var ck = C[d.key];
        if (ck) Object.keys(ck.ws).forEach(function (r) { try { ck.ws[r].ws.close(4000, 'test'); } catch (e) { /* zu */ } });
      }
      else if (d.a === 'cfg') { var u = conn(d.key).up; u.max = d.maxKbps || u.max; u.min = d.minKbps || u.min; u.target = Math.min(u.target, u.max); }
      else if (d.a === 'drop') {
        var c = C[d.key];
        if (c) { Object.keys(c.ws).forEach(function (r) { closeW(c.ws[r], 1000); }); delete C[d.key]; }
      } else if (d.a === 'stats') X.post({ id: d.id, stats: { cnt: cnt, conns: Object.keys(C).length, ws: Object.keys(WS).length } });
      return true;
    };
    root.SagaHornFrames = Object.assign({ init: function (ctx) { X = ctx; return { transform: transform, msg: msg }; } }, core);
    return;
  }

  // --- Rolle 2: Fenster ------------------------------------------------------------------------------------------------
  var isFF = typeof navigator !== 'undefined' && /Firefox\//.test(navigator.userAgent);
  var AC = null;
  function audioCtx() {
    var A = root.AudioContext || root.webkitAudioContext;
    if (!AC && A) AC = new A();
    if (AC && AC.state === 'suspended' && AC.resume) AC.resume().catch(function () {});
    return AC;
  }
  // Moeglich? Encoded Transform (Script), Canvas-Erfassung, WebSocket, AudioContext
  function supported() {
    return typeof RTCRtpScriptTransform === 'function' && typeof WebSocket === 'function' && !!(root.AudioContext || root.webkitAudioContext) &&
      typeof HTMLCanvasElement !== 'undefined' && typeof HTMLCanvasElement.prototype.captureStream === 'function';
  }
  // Schleifen-SDP: Dependency-/Generic-Frame-Descriptor raus (Rahmenart und Groesse nimmt pcY aus dem VP8-Bitstrom),
  // Chromium: hohe Mindestrate (der Pacer bremst die ersetzten, grossen Rahmen nicht); ptime 40 fuer den eigenen Ton (wie
  // TCP-Wege). Firefox ignoriert x-google-* (dort maxBitrate an den Attrappen).
  function munge(sdp, ptimeMid) {
    var out = [], sec = null;
    sdp.split('\r\n').forEach(function (l) {
      if (/dependency-descriptor|generic-frame-descriptor|video-layers-allocation/.test(l)) return;
      if (l.indexOf('m=') === 0) sec = { audio: l.indexOf('m=audio') === 0 };
      out.push(l);
      var m = /^a=rtpmap:(\d+) VP8\/90000/.exec(l);
      if (m && !isFF) out.push('a=fmtp:' + m[1] + ' x-google-min-bitrate=6000;x-google-start-bitrate=8000;x-google-max-bitrate=30000');
      if (sec && sec.audio && ptimeMid != null && l === 'a=mid:' + ptimeMid) out.push('a=ptime:40');
    });
    return out.join('\r\n');
  }
  function prefer(tr, kind) {
    try {
      var caps = RTCRtpSender.getCapabilities(kind), want = kind === 'audio' ? 'audio/opus' : 'video/vp8';
      var cs = (caps && caps.codecs || []).filter(function (c) { return c.mimeType.toLowerCase() === want; });
      if (cs.length && tr.setCodecPreferences) tr.setCodecPreferences(cs);
    } catch (e) { /* aelterer Browser: Standard (Saga erzwingt VP8 ueber den Codec-Filter der E2E) */ }
  }

  // create(X, o): Schleife einer Knoten-Verbindung. X = SagaFrameE2E-Instanz (Worker mit Schluesseln), o = {key, tiles,
  // audioTrack, camTrack, screenTrack, tx: {l, m, screen, screenFps} (kbit/s), jb (jitterBufferTarget Bild, ms), rseq()}.
  function create(X, o) {
    var key = o.key, ev = {}, closed = false, seq = 0, waits = {};
    var pcX = new RTCPeerConnection(), pcY = new RTCPeerConnection();
    X.worker.postMessage({ op: 'frames', url: o.url || '/static/horn-frames.js' });
    var F = { key: key, pcX: pcX, pcY: pcY, tx: {}, rx: { audio: [], video: [] }, state: 'new', rate: null, dummies: [], st: {}, onRate: null,
              onKf: null };
    var tf = function (x, opt) { x.transform = new RTCRtpScriptTransform(X.worker, Object.assign({ key: key }, opt)); };
    // --- eigene Spuren (tap) ---
    var tracks = { audio: o.audioTrack || null, cam: o.camTrack || null, screen: o.screenTrack || null };
    var camClone = null;
    var camL = function (t) { if (camClone) { camClone.stop(); camClone = null; } if (t) camClone = t.clone(); return camClone; };
    F.tx.audio = pcX.addTransceiver(tracks.audio || 'audio', { direction: 'sendonly' });
    prefer(F.tx.audio, 'audio'); tf(F.tx.audio.sender, { side: 'tap', track: TRACK.audio });
    var tx = o.tx || { l: 150, m: 600, screen: 900, screenFps: 8 };
    F.tx.l = pcX.addTransceiver(tracks.cam ? camL(tracks.cam) : 'video', { direction: 'sendonly', sendEncodings: [Object.assign({ scaleResolutionDownBy: 4,
      maxBitrate: tx.l * 1000, maxFramerate: 20 }, isFF ? {} : { scalabilityMode: 'L1T2' })] });
    F.tx.m = pcX.addTransceiver(tracks.cam || 'video', { direction: 'sendonly', sendEncodings: [Object.assign({ scaleResolutionDownBy: 1,
      maxBitrate: tx.m * 1000, maxFramerate: 30 }, isFF ? {} : { scalabilityMode: 'L1T3' })] });
    F.tx.screen = pcX.addTransceiver(tracks.screen || 'video', { direction: 'sendonly', sendEncodings: [{ maxBitrate: tx.screen * 1000, maxFramerate: tx.screenFps }] });
    [['l', TRACK.l, 1], ['m', TRACK.m, 2], ['screen', TRACK.screen, 0]].forEach(function (x) {
      prefer(F.tx[x[0]], 'video'); tf(F.tx[x[0]].sender, { side: 'tap', track: x[1], top: isFF ? 0 : x[2], fwd: isFF && x[0] !== 'screen' });
    });
    // --- Attrappen je Empfangsplatz (inject) ---
    var ac = audioCtx(), silent = null;
    if (ac) {
      var osc = ac.createOscillator(), g = ac.createGain(), dst = ac.createMediaStreamDestination();
      g.gain.value = 0; osc.connect(g).connect(dst); osc.start();
      silent = { osc: osc, dst: dst };
    }
    var dummyA = [], dummyV = [];
    for (var i = 0; i < 3; i++) {
      var at = silent ? silent.dst.stream.getAudioTracks()[0].clone() : 'audio';
      var ta = pcX.addTransceiver(at, { direction: 'sendonly' });
      prefer(ta, 'audio');
      dummyA.push({ tr: ta, track: typeof at === 'string' ? null : at, rid: o.rseq ? o.rseq() : 0 });
      tf(ta.sender, { side: 'inject', kind: 'audio', role: 'audio', slot: i, rid: dummyA[i].rid });
    }
    var nv = 2 + (o.tiles || 0);                       // Sprecher, Bildschirm, Kacheln (Reihenfolge der Plaetze am Knoten)
    for (i = 0; i < nv; i++) {
      var cv = document.createElement('canvas'); cv.width = 16; cv.height = 16;
      var cs = cv.captureStream(0), vt = cs.getVideoTracks()[0];
      var dv = { cv: cv, g: cv.getContext('2d'), track: vt, n: 0, rid: o.rseq ? o.rseq() : 0,
                 req: typeof vt.requestFrame === 'function' ? function (t) { return function () { t.requestFrame(); }; }(vt) : function (s) { return function () { s.requestFrame(); }; }(cs) };
      // Firefox leitet die Hoechstrate der Schleife aus der Groesse der Attrappe ab: ohne hohes maxBitrate staut der Pacer
      dv.tr = pcX.addTransceiver(vt, Object.assign({ direction: 'sendonly' }, isFF ? { sendEncodings: [{ maxBitrate: 20000000 }] } : {}));
      prefer(dv.tr, 'video');
      tf(dv.tr.sender, { side: 'inject', kind: 'video', role: 'video', slot: i, rid: dv.rid });
      dummyV.push(dv);
    }
    F.dummies = dummyV;
    var mids = {};
    var nego = (async function () {
      pcX.onicecandidate = function (e) { if (e.candidate) pcY.addIceCandidate(e.candidate).catch(function () {}); };
      pcY.onicecandidate = function (e) { if (e.candidate) pcX.addIceCandidate(e.candidate).catch(function () {}); };
      if (isFF) pcY.ontrack = function (e) {            // Firefox: Rahmen der eigenen Bild-Spuren an pcY verwerfen (sink)
        var mid = e.transceiver && e.transceiver.mid;
        if (mid === F.tx.l.mid || mid === F.tx.m.mid) e.receiver.transform = new RTCRtpScriptTransform(X.worker, { side: 'sink', key: key });
      };
      var off = await pcX.createOffer();
      await pcX.setLocalDescription(off);
      await pcY.setRemoteDescription({ type: 'offer', sdp: munge(off.sdp) });
      var ans = await pcY.createAnswer();
      await pcY.setLocalDescription(ans);
      await pcX.setRemoteDescription({ type: 'answer', sdp: munge(ans.sdp, F.tx.audio.mid) });
      pcY.getTransceivers().forEach(function (t) { mids[t.mid] = t; });
      var rx = function (d, kind) {
        var t = mids[d.tr.mid];
        if (!t) throw new Error('frames: schleife');
        t.rid = d.rid;
        if (kind === 'video' && o.jb >= 0) { try { if ('jitterBufferTarget' in t.receiver) t.receiver.jitterBufferTarget = o.jb; } catch (e) { /* aelter */ } }
        return t;
      };
      F.rx.audio = dummyA.map(function (d) { return rx(d, 'audio'); });
      F.rx.video = dummyV.map(function (d) { return rx(d, 'video'); });
      await new Promise(function (ok, bad) {
        var t0 = Date.now(), iv = setInterval(function () {
          var s = pcX.connectionState;
          if (s === 'connected') { clearInterval(iv); ok(); }
          else if (s === 'failed' || s === 'closed' || closed || Date.now() - t0 > 5000) { clearInterval(iv); bad(new Error('frames: schleife ' + s)); }
        }, 20);
      });
      F.state = 'up';
    })();
    nego.catch(function () { F.state = 'failed'; });
    F.ready = nego;

    // --- Nachrichten des Workers ---
    var onEv = function (d) {
      if (closed || d.key !== key) return;
      if (d.t === 'dummy') { var x = dummyV[d.slot]; if (x) { x.n++; x.g.fillStyle = x.n & 1 ? '#111' : '#222'; x.g.fillRect(0, 0, 16, 16); x.req(); } return; }
      if (d.t === 'kf') { if (F.onKf) F.onKf(d.track); else F.kick(d.track); return; }
      if (d.t === 'rate') { F.rate = d.kbps; if (F.onRate) F.onRate(d.kbps); return; }
      var w = waits[d.id];
      if (d.t === 'st') { F.st[d.role] = d; return; }
      if (d.t === 'ok' && w) { w.okd = true; w.ok(d.ok); return; }
      if (d.t === 'err' && w) { w.code = d.code; return; }
      if (d.t === 'closed' && w) {
        delete waits[d.id];
        if (!w.okd) { var e = new Error('ws ' + (w.code || d.code)); e.code = w.code || ''; e.close = d.code; w.bad(e); }
        else if (w.onClose && !w.mine) w.onClose(w.code || '', d.code);
      }
    };
    X.on('fr', onEv);
    // WebSocket einer Leitung oeffnen -> Promise(ok-Antwort); h.close() schliesst, h.onClose(code, closeCode) bei Abbruch
    F.open = function (role, url, join) {
      var id = key + ':' + role + ':' + (++seq), h = { id: id };
      var p = new Promise(function (ok, bad) { waits[id] = { ok: ok, bad: bad, okd: false, onClose: null }; });
      X.worker.postMessage({ op: 'fr', a: 'open', id: id, key: key, role: role, url: url, join: join });
      h.ok = p;
      h.close = function () { var w = waits[id]; if (w) w.mine = true; X.worker.postMessage({ op: 'fr', a: 'close', id: id }); };
      h.on = function (fn) { var w = waits[id]; if (w) w.onClose = fn; };
      return h;
    };
    F.cfg = function (maxKbps, minKbps) { X.worker.postMessage({ op: 'fr', a: 'cfg', key: key, maxKbps: maxKbps, minKbps: minKbps }); };
    // Chromium: Schluesselbild nur per Neukonfiguration (active aus und an) - nur wenn die Schicht gerade aktiv ist
    var kickAt = {};
    F.kick = function (track) {
      var t = track === TRACK.l ? F.tx.l : track === TRACK.m ? F.tx.m : track === TRACK.screen ? F.tx.screen : null, now = Date.now();
      if (!t || !t.sender.track || now - (kickAt[track] || 0) < K.KF_MIN) return;
      kickAt[track] = now;
      var p = t.sender.getParameters();
      if (!p.encodings || !p.encodings.length || p.encodings[0].active === false) return;
      p.encodings[0].active = false;
      t.sender.setParameters(p).then(function () {
        var q = t.sender.getParameters(); q.encodings[0].active = true; return t.sender.setParameters(q);
      }).catch(function () {});
    };
    // Kamera-Sender wie ein Simulcast-Sender (rids l, m) fuer calls.js (hornTuneCam, replaceTrack): l bekommt eine Kopie
    // der Spur; die Rate von m deckelt zusaetzlich die Aufwaerts-Regelung (F.rate)
    var plan = { l: {}, m: {} };
    var applyEnc = function (rid) {
      var t = F.tx[rid], w = plan[rid], p = t.sender.getParameters();
      if (!p.encodings || !p.encodings.length) return Promise.resolve();
      var e = p.encodings[0], d = false;
      ['active', 'maxFramerate', 'scaleResolutionDownBy'].forEach(function (k) { if (w[k] !== undefined && e[k] !== w[k]) { e[k] = w[k]; d = true; } });
      var mb = w.maxBitrate !== undefined ? w.maxBitrate : e.maxBitrate;
      if (rid === 'm' && F.rate && mb) mb = Math.min(mb, F.rate * 1000);
      if (mb && e.maxBitrate !== mb) { e.maxBitrate = mb; d = true; }
      return d ? t.sender.setParameters(p) : Promise.resolve();
    };
    F.cam = { sender: {
      getParameters: function () {
        return { encodings: ['l', 'm'].map(function (rid) {
          var e = (F.tx[rid].sender.getParameters().encodings || [{}])[0] || {};
          return Object.assign({ rid: rid }, { active: plan[rid].active !== undefined ? plan[rid].active : e.active !== false,
                                               maxBitrate: plan[rid].maxBitrate !== undefined ? plan[rid].maxBitrate : e.maxBitrate,
                                               maxFramerate: e.maxFramerate, scaleResolutionDownBy: e.scaleResolutionDownBy });
        }) };
      },
      setParameters: function (p) {
        (p.encodings || []).forEach(function (e) { if (plan[e.rid]) ['active', 'maxBitrate', 'maxFramerate', 'scaleResolutionDownBy'].forEach(function (k) { if (e[k] !== undefined) plan[e.rid][k] = e[k]; }); });
        return Promise.all([applyEnc('l'), applyEnc('m')]);
      },
      replaceTrack: function (t) { tracks.cam = t; return Promise.all([F.tx.l.sender.replaceTrack(t ? camL(t) : camL(null)), F.tx.m.sender.replaceTrack(t)]); },
      getStats: function () { return F.tx.m.sender.getStats(); },
      get track() { return tracks.cam; }
    } };
    F.applyRate = function () { return applyEnc('m').catch(function () {}); };
    // Pegel des eigenen Mikrofons (audio-level der Ton-Rahmen: Top-3-Sprecher am Knoten), alle 60 ms
    var meter = null, mT = null;
    F.mic = function (t) {
      if (meter) { try { meter.src.disconnect(); } catch (e) { /* weg */ } meter = null; }
      if (!t || !ac) return;
      try {
        var src = ac.createMediaStreamSource(new MediaStream([t])), an = ac.createAnalyser();
        an.fftSize = 512; src.connect(an);
        meter = { src: src, an: an, buf: new Float32Array(512), t: t };
      } catch (e) { meter = null; }
    };
    F.mic(tracks.audio);
    mT = setInterval(function () {
      var v = 127;
      if (meter && meter.t.enabled && meter.t.readyState === 'live') {
        meter.an.getFloatTimeDomainData(meter.buf);
        var s = 0; for (var k = 0; k < meter.buf.length; k++) s += meter.buf[k] * meter.buf[k];
        var rms = Math.sqrt(s / meter.buf.length);
        v = rms > 0 ? Math.max(0, Math.min(127, Math.round(-20 * Math.log10(rms)))) : 127;
      }
      X.worker.postMessage({ op: 'fr', a: 'level', v: (v < 50 ? 0x80 : 0) | v });
    }, 60);
    F.close = function () {
      if (closed) return;
      closed = true; clearInterval(mT);
      if (meter) { try { meter.src.disconnect(); } catch (e) { /* weg */ } }
      X.worker.postMessage({ op: 'fr', a: 'drop', key: key });
      if (X.off) X.off('fr', onEv);
      Object.keys(waits).forEach(function (id) { waits[id].mine = true; });
      try { pcX.close(); } catch (e) { /* zu */ }
      try { pcY.close(); } catch (e) { /* zu */ }
      if (camClone) camClone.stop();
      dummyA.forEach(function (d) { if (d.track) d.track.stop(); });
      dummyV.forEach(function (d) { d.track.stop(); });
      if (silent) { try { silent.osc.stop(); } catch (e) { /* zu */ } }
    };
    return F;
  }
  root.SagaHornFrames = Object.assign({ supported: supported, create: create, munge: munge, isFF: isFF }, core);
})(typeof self !== 'undefined' ? self : globalThis);
