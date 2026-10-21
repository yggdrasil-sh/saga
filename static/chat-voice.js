// Saga - Sprachnachrichten im Chat (2.5, Konzept Chat B3). Von static/chat.js genutzt (window.SagaVoice).
//   Aufnahme: Opus mono 48 kHz, 24 kbit/s, Ogg (audio/ogg; codecs=opus), hoechstens 15 min (Grenzen vom Server, x3_limits).
//     Chromium/Edge (und alle mit WebCodecs-Opus): AudioEncoder + eigener Ogg-Muxer (RFC 7845).
//     Firefox: MediaRecorder audio/ogg;codecs=opus (Ogg direkt).
//     Safari (ohne WebCodecs-Opus): MediaRecorder audio/webm;codecs=opus -> Opus-Pakete aus WebM (EBML) in Ogg umpacken.
//     Wellenform: 64 Werte 0-255 (Spitzen der Lautstaerke je Abschnitt), aus der Aufnahme berechnet.
//   Senden: wie Videos als saga-file/v2 (static/chat-video.js upload), Anhang {voice: true, dur, wf} im verschluesselten Inhalt.
//   Abspielen: Service-Worker-Pfad wie Videos (/_m/<id>, Range), sonst ganz laden; Dauer aus dur (nicht aus dem Element:
//     ohne Range meldet der Browser oft Infinity); Tempo 1x/1,5x/2x, Springen ueber die Wellenform, danach die naechste.
(function () {
  'use strict';
  var T = function (s, o) { return window.T ? window.T(s, o) : s; };
  var RATE = 48000, WF_N = 64;

  // --- Ogg-Kapselung (RFC 3533/7845) ----------------------------------------------------------------------------------
  var CRC = (function () {
    var t = new Uint32Array(256);
    for (var i = 0; i < 256; i++) { var r = i << 24; for (var j = 0; j < 8; j++) r = r & 0x80000000 ? (r << 1) ^ 0x04c11db7 : r << 1; t[i] = r >>> 0; }
    return t;
  })();
  function crc(b) { var c = 0; for (var i = 0; i < b.length; i++) c = ((c << 8) ^ CRC[((c >>> 24) ^ b[i]) & 255]) >>> 0; return c; }
  // Samples (48 kHz) eines Opus-Pakets aus dem TOC-Byte (RFC 6716, 3.1)
  function opusSamples(p) {
    if (!p || !p.length) return 0;
    var cfg = p[0] >> 3, c = p[0] & 3, n = c === 0 ? 1 : c === 3 ? (p.length > 1 ? p[1] & 63 : 0) : 2, us;
    if (cfg < 12) us = [10000, 20000, 40000, 60000][cfg & 3];
    else if (cfg < 16) us = [10000, 20000][cfg & 1];
    else us = [2500, 5000, 10000, 20000][cfg & 3];
    return n * us * RATE / 1e6;
  }
  // Granule (RFC 7845, 4): Zahl aller dekodierten Samples einschliesslich pre-skip - die ersten Pakete enthalten den
  // pre-skip schon, gezaehlt wird also ab 0; Dauer = (letzte Granule - pre-skip) / 48000. endSamples (Zahl der
  // aufgenommenen Samples, falls bekannt): letzte Granule = pre-skip + endSamples (Ende abschneiden, RFC 7845 4.4).
  function oggOpus(pkts, preskip, vendorName, endSamples) {
    var pages = [], seq = 0, serial = (Math.random() * 0xffffffff) >>> 0;
    function page(packets, granule, flags) {
      var lace = [], len = 0;
      packets.forEach(function (p) { var n = p.length; while (n >= 255) { lace.push(255); n -= 255; } lace.push(n); len += p.length; });
      var b = new Uint8Array(27 + lace.length + len), dv = new DataView(b.buffer);
      b.set([0x4f, 0x67, 0x67, 0x53, 0, flags]);
      dv.setUint32(6, granule % 4294967296, true); dv.setUint32(10, Math.floor(granule / 4294967296), true);
      dv.setUint32(14, serial, true); dv.setUint32(18, seq++, true);
      b[26] = lace.length; b.set(lace, 27);
      var o = 27 + lace.length; packets.forEach(function (p) { b.set(p, o); o += p.length; });
      dv.setUint32(22, crc(b), true); pages.push(b);
    }
    var te = new TextEncoder(), head = new Uint8Array(19), hd = new DataView(head.buffer);
    head.set(te.encode('OpusHead')); head[8] = 1; head[9] = 1; hd.setUint16(10, preskip, true); hd.setUint32(12, RATE, true);
    page([head], 0, 2);
    var vendor = te.encode(vendorName || 'Saga'), tags = new Uint8Array(8 + 4 + vendor.length + 4), td = new DataView(tags.buffer);
    tags.set(te.encode('OpusTags')); td.setUint32(8, vendor.length, true); tags.set(vendor, 12);
    page([tags], 0, 0);
    var g = 0, cur = [], size = 0, last = endSamples != null ? preskip + endSamples : Infinity;
    for (var i = 0; i < pkts.length; i++) {               // Seiten bis ~1 s bzw. 255 Segmente
      var p = pkts[i]; g += opusSamples(p); cur.push(p); size += Math.ceil((p.length + 1) / 255);
      if (cur.length >= 50 || size > 240 || i === pkts.length - 1) { page(cur, i === pkts.length - 1 ? Math.min(g, last) : g, i === pkts.length - 1 ? 4 : 0); cur = []; size = 0; }
    }
    if (!pkts.length) page([], g, 4);
    return new Blob(pages, { type: 'audio/ogg; codecs=opus' });
  }

  // --- WebM (EBML) -> Opus-Pakete (MediaRecorder in Safari/Chromium; ein Audio-Track, ohne Lacing) --------------------------
  function vint(b, o, keep) {
    var x = b[o], n = 1, m = 0x80;
    while (n <= 8 && !(x & m)) { n++; m >>= 1; }
    if (n > 8 || o + n > b.length) return null;
    var v = keep ? x : x & (m - 1), all = (x & (m - 1)) === m - 1;
    for (var i = 1; i < n; i++) { v = v * 256 + b[o + i]; if (b[o + i] !== 255) all = false; }
    return { v: v, n: n, unknown: !keep && all };
  }
  var MASTER = { 0x18538067: 1, 0x1F43B675: 1, 0xA0: 1 };     // Segment, Cluster, BlockGroup: hineingehen
  function webmOpus(buf) {
    var b = new Uint8Array(buf), o = 0, out = [], head = null;
    while (o < b.length) {
      var id = vint(b, o, true); if (!id) break;
      var sz = vint(b, o + id.n, false); if (!sz) break;
      var d = o + id.n + sz.n;
      if (MASTER[id.v]) { o = d; continue; }
      if (sz.unknown) break;
      if (id.v === 0xA3 || id.v === 0xA1) {               // SimpleBlock / Block: Track, Zeit (2), Flags, Daten
        var tr = vint(b, d, false), fl = b[d + tr.n + 2];
        if (((fl >> 1) & 3) === 0) out.push(b.slice(d + tr.n + 3, d + sz.v));
      } else if (id.v === 0x63A2) head = b.slice(d, d + sz.v);   // CodecPrivate (OpusHead): pre-skip
      o = d + sz.v;
    }
    var preskip = head && head.length >= 12 ? head[10] | (head[11] << 8) : 312;
    return { pkts: out, preskip: preskip };
  }

  // --- Wellenform -------------------------------------------------------------------------------------------------------
  function waveform(levels) {
    var out = new Uint8Array(WF_N), n = levels.length;
    if (!n) return out;
    var max = 0, v = [];
    for (var i = 0; i < WF_N; i++) {
      var a = Math.floor(i * n / WF_N), z = Math.max(a + 1, Math.floor((i + 1) * n / WF_N)), m = 0;
      for (var k = a; k < z && k < n; k++) m = Math.max(m, levels[k]);
      v.push(m); max = Math.max(max, m);
    }
    for (var j = 0; j < WF_N; j++) out[j] = max > 0 ? Math.round(255 * Math.sqrt(v[j] / max)) : 0;
    return out;
  }

  // --- Aufnahme ---------------------------------------------------------------------------------------------------------
  var MR_OGG = 'audio/ogg;codecs=opus', MR_WEBM = 'audio/webm;codecs=opus';
  function wcCfg(kbps) { return { codec: 'opus', sampleRate: RATE, numberOfChannels: 1, bitrate: (kbps || 24) * 1000, opus: { frameDuration: 20000, application: 'voip' } }; }
  // -> Promise<'wc'|'mr-ogg'|'mr-webm'|null>
  var modeP = null;
  function mode() {
    if (modeP) return modeP;
    var mr = typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported;
    var fallback = function () { return mr && MediaRecorder.isTypeSupported(MR_OGG) ? 'mr-ogg' : mr && MediaRecorder.isTypeSupported(MR_WEBM) ? 'mr-webm' : null; };
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return (modeP = Promise.resolve(null));
    if (typeof AudioEncoder === 'undefined' || typeof AudioData === 'undefined') return (modeP = Promise.resolve(fallback()));
    modeP = AudioEncoder.isConfigSupported(wcCfg(24)).then(function (s) { return s && s.supported ? 'wc' : fallback(); }, fallback);
    return modeP;
  }
  // opts: {kbps, maxS, onTick(sec, level 0..1), onLimit()} -> Promise<rec>; rec.stop() -> Promise<{blob, dur, wf}>, rec.cancel()
  function record(opts) {
    opts = opts || {};
    var maxS = opts.maxS || 900;
    return (opts.mode ? Promise.resolve(opts.mode) : mode()).then(function (md) {      // opts.mode: Weg erzwingen (Tests)
      if (!md) throw Object.assign(new Error(T('Dieser Browser kann keine Sprachnachrichten aufnehmen.')), { code: 'unsupported' });
      return navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } }).then(function (stream) {
        var AC = window.AudioContext || window.webkitAudioContext, ac;
        try { ac = new AC(md === 'wc' ? { sampleRate: RATE } : {}); } catch (e) { ac = new AC(); }
        if (md === 'wc' && ac.sampleRate !== RATE) { try { ac.close(); } catch (e) { /* egal */ } md = MediaRecorder && MediaRecorder.isTypeSupported(MR_OGG) ? 'mr-ogg' : 'mr-webm'; ac = new AC(); }
        var src = ac.createMediaStreamSource(stream), an = ac.createAnalyser(); an.fftSize = 1024; src.connect(an);
        var levels = [], t0 = performance.now(), done = false, tick = null, buf = new Float32Array(an.fftSize);
        var enc = null, pkts = [], samples = 0, preskip = 312, proc = null, mute = null, pcm = new Float32Array(960), fill = 0, mr = null, chunks = [];
        if (md === 'wc') {
          enc = new AudioEncoder({ output: function (c, meta) {
            var a = new Uint8Array(c.byteLength); c.copyTo(a); pkts.push(a);
            var ds = meta && meta.decoderConfig && meta.decoderConfig.description;       // OpusHead des Encoders: pre-skip
            if (ds && ds.byteLength >= 12) { var h = new Uint8Array(ds.buffer || ds, ds.byteOffset || 0, ds.byteLength); preskip = h[10] | (h[11] << 8); }
          }, error: function () { /* stop() meldet */ } });
          enc.configure(wcCfg(opts.kbps));
          proc = ac.createScriptProcessor(4096, 1, 1); mute = ac.createGain(); mute.gain.value = 0;
          src.connect(proc); proc.connect(mute); mute.connect(ac.destination);
          proc.onaudioprocess = function (e) {
            if (done) return;
            var x = e.inputBuffer.getChannelData(0);
            for (var i = 0; i < x.length; i++) {
              pcm[fill++] = x[i];
              if (fill === 960) {
                enc.encode(new AudioData({ format: 'f32', sampleRate: RATE, numberOfFrames: 960, numberOfChannels: 1, timestamp: samples * 1e6 / RATE, data: pcm }));
                samples += 960; fill = 0;
              }
            }
          };
        } else {
          mr = new MediaRecorder(stream, { mimeType: md === 'mr-ogg' ? MR_OGG : MR_WEBM, audioBitsPerSecond: (opts.kbps || 24) * 1000 });
          mr.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
          mr.start(1000);
        }
        var rec = { mode: md };
        function secs() { return md === 'wc' ? samples / RATE : (performance.now() - t0) / 1000; }
        function release() {
          done = true; clearInterval(tick);
          stream.getTracks().forEach(function (t) { t.stop(); });
          try { src.disconnect(); if (proc) proc.disconnect(); } catch (e) { /* egal */ }
          try { ac.close(); } catch (e) { /* egal */ }
        }
        tick = setInterval(function () {
          an.getFloatTimeDomainData(buf);
          var s = 0; for (var i = 0; i < buf.length; i++) s += buf[i] * buf[i];
          var lv = Math.sqrt(s / buf.length); levels.push(lv);
          var sec = secs();
          if (opts.onTick) opts.onTick(sec, Math.min(1, lv * 4));
          if (sec >= maxS && !done && opts.onLimit) opts.onLimit();
        }, 100);
        rec.cancel = function () { if (done) return; if (mr && mr.state !== 'inactive') try { mr.stop(); } catch (e) { /* egal */ } if (enc) try { enc.close(); } catch (e) { /* egal */ } release(); };
        rec.stop = function () {
          if (done) return Promise.reject(new Error('stopped'));
          var wall = (performance.now() - t0) / 1000;
          if (md === 'wc') {
            done = true;
            if (fill) { var last = new Float32Array(960); last.set(pcm.subarray(0, fill));
              enc.encode(new AudioData({ format: 'f32', sampleRate: RATE, numberOfFrames: 960, numberOfChannels: 1, timestamp: samples * 1e6 / RATE, data: last })); samples += fill; }
            release();
            return enc.flush().then(function () {
              try { enc.close(); } catch (e) { /* egal */ }
              return { blob: oggOpus(pkts, preskip, null, samples), dur: Math.min(maxS, samples / RATE), wf: waveform(levels), mode: md };
            });
          }
          return new Promise(function (res, rej) {
            mr.onstop = function () {
              release();
              var raw = new Blob(chunks, { type: mr.mimeType || (md === 'mr-ogg' ? MR_OGG : MR_WEBM) });
              if (md === 'mr-ogg') { res({ blob: new Blob([raw], { type: 'audio/ogg; codecs=opus' }), dur: Math.min(maxS, wall), wf: waveform(levels), mode: md }); return; }
              raw.arrayBuffer().then(function (ab) {
                var w = webmOpus(ab), n = 0;
                if (!w.pkts.length) throw new Error(T('Aufnahme leer.'));
                w.pkts.forEach(function (p) { n += opusSamples(p); });
                res({ blob: oggOpus(w.pkts, w.preskip), dur: Math.min(maxS, Math.max(0, n - w.preskip) / RATE), wf: waveform(levels), mode: md });
              }).catch(rej);
            };
            try { mr.stop(); } catch (e) { rej(e); }
          });
        };
        rec.secs = secs;
        return rec;
      });
    });
  }

  // --- Wiedergabe in der Nachricht --------------------------------------------------------------------------------------
  // deps: {el, media(f) -> Promise<url|null>, plain(f) -> Promise<ArrayBuffer>, drop(url), b64dec(s) -> Uint8Array}
  var D = null, CUR = null, SPEEDS = [1, 1.5, 2];
  function fmt(s) { s = Math.max(0, Math.round(+s || 0)); return Math.floor(s / 60) + ':' + ('0' + s % 60).slice(-2); }
  function speed() { try { var v = +localStorage.getItem('saga-voice-speed'); return SPEEDS.indexOf(v) >= 0 ? v : 1; } catch (e) { return 1; } }
  function setSpeed(v) { try { localStorage.setItem('saga-voice-speed', String(v)); } catch (e) { /* egal */ } }
  function wfOf(f) {
    try { var w = D.b64dec(f.wf || ''); if (w && w.length) return w; } catch (e) { /* ohne Wellenform */ }
    return new Uint8Array(WF_N);
  }
  function player(f) {
    var el = D.el, box = el('div', 'vm'), dur = Math.max(0, +f.dur || 0);
    box.dataset.fid = f.id;
    var pb = el('button', 'vm-play', '▶'); pb.type = 'button'; pb.setAttribute('aria-label', T('Sprachnachricht abspielen'));
    var wave = el('div', 'vm-wave'), bars = [], wf = wfOf(f);
    wave.setAttribute('role', 'slider'); wave.setAttribute('aria-label', T('Position')); wave.tabIndex = 0;
    wave.setAttribute('aria-valuemin', '0'); wave.setAttribute('aria-valuemax', String(Math.round(dur))); wave.setAttribute('aria-valuenow', '0');
    for (var i = 0; i < WF_N; i++) { var s = el('span'); s.style.height = Math.max(8, Math.round((wf[i] || 0) / 255 * 100)) + '%'; bars.push(s); wave.appendChild(s); }
    var tm = el('span', 'vm-time', fmt(dur)), sp = el('button', 'vm-speed', speed() + '×'); sp.type = 'button';
    sp.setAttribute('aria-label', T('Tempo')); sp.title = T('Tempo');
    box.appendChild(pb); box.appendChild(wave); box.appendChild(tm); box.appendChild(sp);
    var a = null, url = null, loading = null, raf = 0, lastOn = -1;
    function frac() { return a && dur ? Math.min(1, a.currentTime / dur) : 0; }
    function paint() {
      var on = Math.round(frac() * WF_N);
      if (on !== lastOn) { for (var k = 0; k < WF_N; k++) bars[k].classList.toggle('on', k < on); lastOn = on; }
      if (a) { tm.textContent = fmt(a.paused && !a.currentTime ? dur : a.currentTime); wave.setAttribute('aria-valuenow', String(Math.round(a.currentTime))); }
    }
    function loop() { paint(); if (a && !a.paused) raf = requestAnimationFrame(loop); }
    function load() {
      if (loading) return loading;
      pb.disabled = true; pb.textContent = '…';
      loading = (f._url ? Promise.resolve(f._url) : D.media(f)).then(function (u) {      // _url: Vorschau vor dem Senden
        if (u) return u;
        return D.plain(f).then(function (p) { return URL.createObjectURL(new Blob([p], { type: 'audio/ogg; codecs=opus' })); });
      }).then(function (u) {
        url = u; a = new Audio(); a.preload = 'auto'; a.src = u; a.playbackRate = speed();
        a.addEventListener('play', function () { pb.textContent = '❚❚'; pb.setAttribute('aria-label', T('Pause')); box.classList.add('playing'); loop(); });
        a.addEventListener('pause', function () { pb.textContent = '▶'; pb.setAttribute('aria-label', T('Sprachnachricht abspielen')); box.classList.remove('playing'); paint(); });
        a.addEventListener('ended', function () {
          a.currentTime = 0; paint(); tm.textContent = fmt(dur); box.classList.remove('playing');
          var all = Array.prototype.slice.call(document.querySelectorAll('#ct-msgs .vm')), nx = all[all.indexOf(box) + 1];
          if (nx && nx.vmPlay) nx.vmPlay();                // danach die naechste Sprachnachricht
        });
        a.addEventListener('error', function () { pb.textContent = '⚠'; pb.title = T('Sprachnachricht nicht abspielbar.'); });
        pb.disabled = false; pb.textContent = '▶';
        return a;
      }).catch(function (e) { loading = null; pb.disabled = false; pb.textContent = '⚠'; pb.title = e.message; throw e; });
      return loading;
    }
    function play() {
      if (CUR && CUR !== box && CUR.vmPause) CUR.vmPause();
      CUR = box;
      return load().then(function (x) { x.playbackRate = speed(); return x.play(); }).catch(function () { /* Knopf zeigt den Fehler */ });
    }
    box.vmPlay = play;
    box.vmState = function () { return { src: a ? a.src : null, t: a ? a.currentTime : 0, rate: a ? a.playbackRate : speed(), paused: a ? a.paused : true, dur: dur }; };
    box.vmPause = function () { if (a) a.pause(); };
    pb.addEventListener('click', function () { if (a && !a.paused) a.pause(); else play(); });
    function seek(x) { load().then(function (au) { au.currentTime = Math.max(0, Math.min(dur - 0.05, x * dur)); paint(); }).catch(function () {}); }
    wave.addEventListener('click', function (e) { var r = wave.getBoundingClientRect(); seek((e.clientX - r.left) / Math.max(1, r.width)); });
    wave.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      e.preventDefault(); seek(Math.max(0, frac() + (e.key === 'ArrowRight' ? 5 : -5) / Math.max(1, dur)));
    });
    sp.addEventListener('click', function () {
      var v = SPEEDS[(SPEEDS.indexOf(speed()) + 1) % SPEEDS.length]; setSpeed(v);
      Array.prototype.forEach.call(document.querySelectorAll('.vm-speed'), function (b) { b.textContent = v + '×'; });
      if (a) a.playbackRate = v;
    });
    box.vmDrop = function () { cancelAnimationFrame(raf); if (a) { a.pause(); a.removeAttribute('src'); } if (url && !f._url) { if (url.indexOf('blob:') === 0) URL.revokeObjectURL(url); else D.drop(url); } };
    return box;
  }

  window.SagaVoice = { init: function (deps) { D = deps; }, mode: mode, record: record, player: player, fmt: fmt,
                       oggOpus: oggOpus, webmOpus: webmOpus, opusSamples: opusSamples, waveform: waveform, WF_N: WF_N };
})();
