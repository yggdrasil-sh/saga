// Saga - Videos im Chat (2.4, Etappe E2). Von static/chat.js genutzt (window.SagaChatVideo.init(...)).
//   Senden:  Poster (320-px-JPEG, <= 30 KB), Dauer und Abmessungen erzeugt dieses Geraet aus dem Video (Canvas).
//            Optional "optimiert 720p" (MediaRecorder, nur wenn der Browser MP4 aufnehmen kann), sonst Original.
//            Groesser als limits.file_max: Format saga-file/v2 (saga/file_v2.py) mit fortsetzbarem Upload
//            (POST /api/chats/{cid}/uploads, PUT /api/chat/uploads/{id} in Teilen, HEAD zum Fortsetzen). Verschluesselt
//            wird blockweise im Worker (static/chat-worker.js, op f2enc) - die Datei liegt nie ganz im Speicher.
//   Ansehen: Service Worker (static/sw.js) entschluesselt Range-Anfragen blockweise (<video src="/_m/<id>">), Medien-Token
//            je Wiedergabe. Ohne Service Worker: bis 100 MB als Blob, sonst Hinweis und "Herunterladen".
//   Speichern: showSaveFilePicker als Strom, sonst ueber den Service Worker (Download-Strom), zuletzt Blob (<= 500 MB).
// In der Chatliste nur Poster (lazy, wie Bilder) - nie ein <video>-Element ausserhalb des Players.
(function () {
  'use strict';
  var D = null;                                        // {X, api, T, el, run, toast, me()}
  var MiB = 1048576, POSTER_MAX = 320, POSTER_BYTES = 30 * 1024, BLOB_PLAY_MAX = 100 * MiB, BLOB_SAVE_MAX = 500 * MiB;
  var REG = {};                                        // laufende Wiedergaben (fuer den Service Worker nach Neustart)

  function T(s, o) { return D.T(s, o); }
  function lim() { var me = D.me(); return (me && me.limits) || {}; }
  function rnd16hex() { var a = new Uint8Array(16); crypto.getRandomValues(a); return Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join(''); }

  // --- Absender: Metadaten und Poster ---------------------------------------------------------------------------------
  function isVideo(f) { return /^video\//.test((f && (f.type || f.m)) || ''); }
  function probe(file) {
    return new Promise(function (res, rej) {
      var v = document.createElement('video'), url = URL.createObjectURL(file), done = false;
      function fin(err, out) {
        if (done) return; done = true; clearTimeout(tm);
        v.removeAttribute('src'); try { v.load(); } catch (e) { /* egal */ }
        URL.revokeObjectURL(url);
        if (err) rej(err); else res(out);
      }
      var tm = setTimeout(function () { fin(new Error('timeout')); }, 15000);
      v.muted = true; v.playsInline = true; v.preload = 'metadata';
      v.onerror = function () { fin(new Error('codec')); };
      v.onloadedmetadata = function () {
        var dur = isFinite(v.duration) ? v.duration : 0;
        v.currentTime = Math.min(1, dur * 0.1);
      };
      v.onseeked = function () {
        var w = v.videoWidth, h = v.videoHeight;
        if (!w || !h) return fin(null, { dur: v.duration || 0, w: 0, h: 0, jpeg: null });
        var f = Math.min(1, POSTER_MAX / Math.max(w, h)), cv = document.createElement('canvas');
        cv.width = Math.max(1, Math.round(w * f)); cv.height = Math.max(1, Math.round(h * f));
        var g = cv.getContext('2d'); g.drawImage(v, 0, 0, cv.width, cv.height);
        var qs = [0.7, 0.55, 0.4, 0.3];
        (function enc(i) {
          cv.toBlob(function (b) {
            if (!b) return fin(null, { dur: v.duration || 0, w: w, h: h, jpeg: null });
            if (b.size > POSTER_BYTES && i + 1 < qs.length) return enc(i + 1);
            b.arrayBuffer().then(function (buf) {
              fin(null, { dur: Math.round((v.duration || 0) * 10) / 10, w: w, h: h, jpeg: buf, tw: cv.width, th: cv.height });
            });
          }, 'image/jpeg', qs[i]);
        })(0);
      };
      v.src = url;
    });
  }

  // --- optimiert 720p (MediaRecorder; nur MP4 - WebM ohne Dauer/Index liesse sich schlecht spulen) ----------------------
  var MP4 = ['video/mp4;codecs=avc1.4d401f,mp4a.40.2', 'video/mp4;codecs=avc1.42E01E,mp4a.40.2', 'video/mp4;codecs=avc1,mp4a.40.2', 'video/mp4'];
  function canOptimize() {
    if (!window.MediaRecorder || !window.HTMLCanvasElement || !HTMLCanvasElement.prototype.captureStream || !MediaRecorder.isTypeSupported) return '';
    for (var i = 0; i < MP4.length; i++) if (MediaRecorder.isTypeSupported(MP4[i])) return MP4[i];
    return '';
  }
  // Echtzeit: das Video laeuft unsichtbar (ohne Ton nach aussen) einmal durch. onProgress(0..1). signal: abbrechen.
  function optimize(file, meta, onProgress, signal) {
    var mime = canOptimize();
    if (!mime) return Promise.reject(new Error(T('Dieser Browser kann Videos nicht verkleinern.')));
    return new Promise(function (res, rej) {
      var v = document.createElement('video'), url = URL.createObjectURL(file), w = meta.w || 1280, h = meta.h || 720;
      var f = Math.min(1, 1280 / Math.max(w, h), 720 / Math.min(w, h));
      var cv = document.createElement('canvas'); cv.width = Math.max(2, Math.round(w * f / 2) * 2); cv.height = Math.max(2, Math.round(h * f / 2) * 2);
      var g = cv.getContext('2d'), ac = null, chunks = [], rec = null, stopped = false;
      function clean() {
        stopped = true; v.pause(); v.removeAttribute('src'); try { v.load(); } catch (e) { /* egal */ }
        URL.revokeObjectURL(url); if (ac) ac.close().catch(function () {});
      }
      if (signal) signal.addEventListener('abort', function () { if (rec && rec.state !== 'inactive') rec.stop(); clean(); rej(new DOMException('abgebrochen', 'AbortError')); });
      v.playsInline = true; v.preload = 'auto'; v.src = url;
      v.onerror = function () { clean(); rej(new Error('codec')); };
      v.oncanplay = function () {
        v.oncanplay = null;
        var vs = cv.captureStream(30), tracks = vs.getVideoTracks();
        try {                                            // Ton: ueber Web Audio abgreifen (nicht hoerbar)
          ac = new (window.AudioContext || window.webkitAudioContext)();
          var src = ac.createMediaElementSource(v), dst = ac.createMediaStreamDestination();
          src.connect(dst);
          tracks = tracks.concat(dst.stream.getAudioTracks());
        } catch (e) { /* ohne Ton */ }
        rec = new MediaRecorder(new MediaStream(tracks), { mimeType: mime, videoBitsPerSecond: 2500000, audioBitsPerSecond: 128000 });
        rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
        rec.onstop = function () {
          if (signal && signal.aborted) return;
          clean();
          var name = String(file.name || 'video').replace(/\.[^.]+$/, '') + '-720p.mp4';
          res(new File(chunks, name, { type: 'video/mp4' }));
        };
        function frame() {
          if (stopped) return;
          g.drawImage(v, 0, 0, cv.width, cv.height);
          if (onProgress && v.duration) onProgress(Math.min(1, v.currentTime / v.duration));
          if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(frame); else requestAnimationFrame(frame);
        }
        v.onended = function () { if (rec.state !== 'inactive') rec.stop(); };
        rec.start(1000);
        v.play().then(frame, function (e) { clean(); rej(e); });
      };
    });
  }

  // --- Upload saga-file/v2 (fortsetzbar) ---------------------------------------------------------------------------------
  // Ciphertext-Bytes from..to (einschliesslich) erzeugen: betroffene Bloecke aus der Datei lesen und (im Worker)
  // verschluesseln - deterministisch (K, Praefix, Blocknummer), daher kann ein Upload an jeder Stelle fortgesetzt werden.
  function cipherPart(file, enc, from, to) {
    var cs = enc.info.cs, X = D.X, out = new Uint8Array(to - from + 1), pos = 0, p = Promise.resolve();
    if (from < 48) {
      var hd = enc.header.subarray(from, Math.min(48, to + 1)); out.set(hd, 0); pos = hd.length;
    }
    var a = Math.max(from, 48);
    if (a > to) return Promise.resolve(out);
    var i = Math.floor((a - 48) / (cs + 16)), j = Math.floor((to - 48) / (cs + 16));
    return p.then(function () { return file.slice(i * cs, Math.min((j + 1) * cs, file.size)).arrayBuffer(); }).then(function (buf) {
      return D.run('f2enc', { key: enc.key, prefix: enc.info.prefix.slice().buffer, cs: cs, size: file.size, i: i, bytes: buf }, [buf]);
    }).then(function (r) {
      var ct = new Uint8Array(r.ct), base = X.f2Off(i, cs);
      out.set(ct.subarray(a - base, to - base + 1), pos);
      return out;
    });
  }
  function put(url, body, from, to, total, signal) {
    return fetch(url, { method: 'PUT', credentials: 'same-origin', signal: signal,
                        headers: { 'X-Saga': '1', 'Content-Type': 'application/octet-stream', 'Content-Range': 'bytes ' + from + '-' + to + '/' + total }, body: body })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { d.status = r.status; return d; }); });
  }
  function headOff(url) {
    return fetch(url, { method: 'HEAD', credentials: 'same-origin', headers: { 'X-Saga': '1' } }).then(function (r) {
      if (r.status === 404) { var e = new Error(T('Upload abgebrochen.')); e.gone = true; throw e; }
      return { off: +(r.headers.get('Upload-Offset') || 0), done: r.headers.get('Upload-Done') === '1' };
    });
  }
  // -> {id, key, cs, size(Klartext), blob(Ciphertext-Groesse)}; onProgress(0..1)
  function upload(cid, file, onProgress, signal) {
    var X = D.X, enc = X.f2Encryptor(file.size), total = X.f2Size(file.size, enc.info.cs), up = null;
    return D.api('POST', '/api/chats/' + cid + '/uploads', { size: total, fmt: 2 }).then(function (d) {
      up = d;
      var url = d.url || ('/api/chat/uploads/' + d.upload), part = Math.min(d.part_max || 8 * MiB, 8 * MiB), off = d.offset || 0, fails = 0;
      if (signal) signal.addEventListener('abort', function () { D.api('DELETE', '/api/chat/uploads/' + up.upload).catch(function () {}); });
      function step() {
        if (signal && signal.aborted) return Promise.reject(new DOMException('abgebrochen', 'AbortError'));
        var to = Math.min(total, off + part) - 1;
        return cipherPart(file, enc, off, to).then(function (body) { return put(url, body, off, to, total, signal); }).then(function (r) {
          if (r.status === 200 && r.done) return r;
          if (r.status === 200) { off = r.offset; fails = 0; if (onProgress) onProgress(off / total); return step(); }
          if (r.status === 409 && typeof r.offset === 'number') { off = r.offset; return step(); }
          if (r.status >= 400 && r.status < 500 && r.status !== 408 && r.status !== 429) throw new Error(r.error || ('HTTP ' + r.status));
          throw new Error('retry');
        }).catch(function (e) {
          if (e.name === 'AbortError' || e.gone || (e.message !== 'retry' && !(e instanceof TypeError))) throw e;
          if (++fails > 8) throw new Error(T('Upload abgebrochen – keine Verbindung.'));
          return new Promise(function (r) { setTimeout(r, Math.min(15000, 500 * Math.pow(2, fails))); })      // Netz weg: warten, Stand holen
            .then(function () { return headOff(url); }).then(function (h) {
              if (h.done) return { id: up.upload, size: total, done: true };
              off = h.off; return step();
            });
        });
      }
      return step();
    }).then(function (r) {
      if (onProgress) onProgress(1);
      return { id: r.id || up.upload, key: enc.key, cs: enc.info.cs, size: file.size, blob: total };
    });
  }

  // --- Wiedergabe ---------------------------------------------------------------------------------------------------------
  var swP = null;
  function sw() {                                       // -> Promise<ServiceWorker-Controller oder null>
    if (swP) return swP;
    if (!('serviceWorker' in navigator) || !window.isSecureContext) return Promise.resolve(null);
    swP = navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(function () { return navigator.serviceWorker.ready; }).then(function () {
      if (navigator.serviceWorker.controller) return navigator.serviceWorker.controller;
      return new Promise(function (res) {               // erste Installation: clients.claim() abwarten
        var t = setTimeout(function () { res(navigator.serviceWorker.controller || null); }, 4000);
        navigator.serviceWorker.addEventListener('controllerchange', function () { clearTimeout(t); res(navigator.serviceWorker.controller); }, { once: true });
      });
    }).catch(function () { return null; });
    swP.then(function (c) { if (!c) swP = null; });
    return swP;
  }
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', function (e) {      // Service Worker neu gestartet: Wiedergabe neu melden
      var d = e.data || {};
      if (d.op === 'saga-media-need' && REG[d.id] && e.ports && e.ports[0]) e.ports[0].postMessage(REG[d.id]);
    });
  }
  function tell(ctl, msg) {
    return new Promise(function (res, rej) {
      var ch = new MessageChannel(), t = setTimeout(function () { rej(new Error('sw')); }, 3000);
      ch.port1.onmessage = function () { clearTimeout(t); res(); };
      ctl.postMessage(msg, [ch.port2]);
    });
  }
  function token(fid) { return D.api('POST', '/api/chat/files/' + encodeURIComponent(fid) + '/token').then(function (d) { return d.token; }); }
  // Wiedergabe beim Service Worker anmelden -> '/_m/<id>' (oder null ohne Service Worker)
  function mediaUrl(f, dl) {
    return sw().then(function (ctl) {
      if (!ctl) return null;
      return token(f.id).then(function (tk) {
        var id = rnd16hex(), msg = { op: 'media', id: id, fid: f.id, key: f.k, size: f.s, cs: f.cs || 262144, m: f.m, token: tk, name: f.n || 'video.mp4' };
        REG[id] = msg;
        return tell(ctl, msg).then(function () { return '/_m/' + id + (dl ? '?dl=1' : ''); });
      });
    });
  }
  function drop(url) {
    var id = String(url || '').slice(4, 36);
    delete REG[id];
    if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage({ op: 'drop', id: id });
  }
  function clear() {                                   // Abmelden / "Neu beginnen": Schluessel im Service Worker vergessen
    REG = {};
    if (navigator.serviceWorker && navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage({ op: 'clear' });
  }
  // ganzer Ciphertext (Fallback ohne Service Worker) -> Klartext (im Worker, blockweise)
  function wholePlain(f, onProgress) {
    return fetch('/api/chat/files/' + encodeURIComponent(f.id), { credentials: 'same-origin', headers: { 'X-Saga': '1' } }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.arrayBuffer();
    }).then(function (buf) {
      var head = buf.slice(0, 48);
      return D.run('f2dec', { key: f.k, head: head, i: 0, ct: buf.slice(48) }, []);
    }).then(function (r) { if (onProgress) onProgress(1); return r.plain; });
  }
  var MIMEOK = /^(video|audio)\/[\w.+-]{1,60}$/;
  // Player im Overlay-Koerper box. -> Funktion zum Aufraeumen
  // 2.4 (Testrunde W8): Format, das dieser Browser nicht abspielen kann (z. B. H.264 in Chromium ohne Lizenz-Codecs, HEVC):
  // klare Meldung mit "Herunterladen" statt eines stummen Players bei 0:00
  function cannot(box, st, v, onSave) {
    if (!box.isConnected || box.querySelector('.ct-vid-cannot')) return;
    try { v.pause(); } catch (e) { /* egal */ }
    v.remove(); st.remove();
    var m = D.el('div', 'ct-vid-cannot'); m.setAttribute('role', 'alert');
    m.appendChild(D.el('span', 'ct-vid-cannot-ic', '🎬'));
    m.appendChild(D.el('p', null, T('Kann im Browser nicht abgespielt werden – herunterladen und mit einem Videoprogramm öffnen.')));
    if (onSave) { var b = D.el('button', 'btn btn-primary', T('Herunterladen')); b.type = 'button'; b.addEventListener('click', onSave); m.appendChild(b); }
    box.appendChild(m);
  }
  function play(box, f, legacyUrl, onSave) {
    var v = document.createElement('video'), st = D.el('p', 'hk-status', T('Lädt …')), cleanup = [];
    v.controls = true; v.playsInline = true; v.preload = 'metadata'; v.className = 'ct-video-player';
    st.setAttribute('role', 'status'); box.appendChild(st);
    if (f.w && f.h) v.style.aspectRatio = f.w + ' / ' + f.h;
    var mt = MIMEOK.test(f.m || '') ? f.m : 'video/mp4';
    if (v.canPlayType && v.canPlayType(mt) === '') { cannot(box, st, v, onSave); return function () {}; }
    var shown = false;
    function show(url) {
      v.src = url; box.appendChild(v); st.remove(); shown = true; v.play().catch(function () {});
      // Container bekannt, Codec nicht: Metadaten ohne Bild bzw. nach 10 s noch nichts abspielbar
      var tm = setTimeout(function () { if (box.isConnected && v.readyState < 2 && !v.error) cannot(box, st, v, onSave); }, 10000);
      cleanup.push(function () { clearTimeout(tm); });
    }
    v.addEventListener('loadedmetadata', function () {
      if (f.w && f.h && !v.videoWidth && !/^audio\//.test(mt)) cannot(box, st, v, onSave);
    });
    var fellBack = false;
    v.addEventListener('error', function () {          // Strom ging nicht (z. B. Safari-Sonderfall): kleinere Videos ganz laden
      var code = v.error && v.error.code;
      if (shown && (code === 3 || code === 4) && (fellBack || f.x !== 2)) { cannot(box, st, v, onSave); return; }
      if (fellBack || f.x !== 2 || !box.isConnected) { if (shown) cannot(box, st, v, onSave); return; }
      fellBack = true;
      if (f.s > BLOB_PLAY_MAX) { box.appendChild(st); st.textContent = T('Video nicht geladen – bitte herunterladen.'); return; }
      box.appendChild(st); st.textContent = T('Lädt …');
      wholePlain(f).then(function (p) {
        var u = URL.createObjectURL(new Blob([p], { type: MIMEOK.test(f.m || '') ? f.m : 'video/mp4' }));
        cleanup.push(function () { URL.revokeObjectURL(u); }); show(u);
      }, function (e) { st.textContent = T('Video nicht geladen: {err}', { err: e.message }); });
    });
    if (f.x !== 2) {                                   // v1 (bis 10 MB): ganz laden, als Blob
      legacyUrl().then(show, function (e) { st.textContent = T('Video nicht geladen: {err}', { err: e.message }); });
      return function () { v.pause(); var u = v.src; v.removeAttribute('src'); v.load(); if (u && u.indexOf('blob:') === 0) URL.revokeObjectURL(u); };
    }
    mediaUrl(f).then(function (url) {
      if (url) { cleanup.push(function () { drop(url); }); return show(url); }
      if (f.s <= BLOB_PLAY_MAX) {                         // ohne Service Worker: kleinere Videos ganz laden
        st.textContent = T('Dieser Browser kann das Video nicht streamen – es wird ganz geladen …');
        return wholePlain(f).then(function (p) {
          var u = URL.createObjectURL(new Blob([p], { type: MIMEOK.test(f.m || '') ? f.m : 'video/mp4' }));
          cleanup.push(function () { URL.revokeObjectURL(u); }); show(u);
        });
      }
      st.textContent = T('Dieser Browser kann große Videos nicht abspielen. Bitte herunterladen.');
    }).catch(function (e) { st.textContent = T('Video nicht geladen: {err}', { err: e.message }); });
    return function () { v.pause(); v.removeAttribute('src'); try { v.load(); } catch (e) { /* egal */ } cleanup.forEach(function (fn) { fn(); }); };
  }

  // --- Speichern als entschluesselte Datei (Strom) -------------------------------------------------------------------------
  function saveStream(f, onProgress, signal) {
    var name = String(f.n || 'video.mp4').slice(0, 200);
    if (window.showSaveFilePicker) {
      return window.showSaveFilePicker({ suggestedName: name }).then(function (h) { return h.createWritable(); }).then(function (w) {
        var cs = f.cs || 262144, head = null, i = 0, n = D.X.f2Blocks(f.s, cs);
        function hdr() {
          return fetch('/api/chat/files/' + encodeURIComponent(f.id), { credentials: 'same-origin', headers: { 'X-Saga': '1', 'Range': 'bytes=0-47' } })
            .then(function (r) { if (r.status !== 206) throw new Error('HTTP ' + r.status); return r.arrayBuffer(); }).then(function (b) { head = b; });
        }
        function next() {
          if (signal && signal.aborted) throw new DOMException('abgebrochen', 'AbortError');
          if (i >= n) return w.close();
          var j = Math.min(n - 1, i + 15), r = D.X.f2Range(f.s, i * cs, Math.max(i * cs, Math.min(f.s, (j + 1) * cs) - 1), cs);
          return fetch('/api/chat/files/' + encodeURIComponent(f.id), { credentials: 'same-origin', headers: { 'X-Saga': '1', 'Range': 'bytes=' + r.from + '-' + r.to } })
            .then(function (x) { if (x.status !== 206) throw new Error('HTTP ' + x.status); return x.arrayBuffer(); })
            .then(function (ct) { return D.run('f2dec', { key: f.k, head: head.slice(0), i: i, ct: ct }, [ct]); })
            .then(function (p) { return w.write(new Uint8Array(p.plain)); })
            .then(function () { i = j + 1; if (onProgress) onProgress(Math.min(1, i / n)); return next(); });
        }
        return hdr().then(next).catch(function (e) { return w.abort().catch(function () {}).then(function () { throw e; }); });
      });
    }
    return mediaUrl(f, true).then(function (url) {
      if (url) {                                       // Download-Strom ueber den Service Worker (Navigation in einem
        var fr = document.createElement('iframe');     // unsichtbaren Rahmen - Content-Disposition: attachment)
        fr.hidden = true; fr.src = url; document.body.appendChild(fr);
        setTimeout(function () { fr.remove(); drop(url); }, 10 * 60 * 1000);
        return;
      }
      if (f.s > BLOB_SAVE_MAX) throw new Error(T('Dieser Browser kann so große Dateien nicht speichern.'));
      return wholePlain(f, onProgress).then(function (p) {
        var u = URL.createObjectURL(new Blob([p], { type: 'application/octet-stream' })), a = document.createElement('a');
        a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(u); }, 60000);
      });
    });
  }

  function dur(s) {
    s = Math.max(0, Math.round(+s || 0));
    var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return (h ? h + ':' + ('0' + m).slice(-2) : m) + ':' + ('0' + x).slice(-2);
  }

  window.SagaChatVideo = {
    init: function (deps) { D = deps; }, isVideo: isVideo, probe: probe, canOptimize: canOptimize, optimize: optimize, upload: upload,
    play: play, save: saveStream, clear: clear, dur: dur, sw: sw
  };
})();
