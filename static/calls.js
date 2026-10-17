// Saga - Anrufe (2.1): 1:1-Audio/Video aus dem Direkt-Chat, WebRTC zwischen den Geraeten, ohne zusaetzlichen Port.
// Signalisierung ueber den Live-Kanal (live.js -> Ereignis 'hk-live': call.ring / call.signal / call.state) und POSTs an
// /api/calls. Jede Signal-Nachricht (SDP, ICE) wird mit dem Chat-Geraeteschluessel signiert und fuer das Zielgeraet
// versiegelt (static/chat-crypto.js, Format saga-call/v1) - der Server sieht weder SDP noch IP-Adressen und kann keinen
// eigenen DTLS-Schluessel unterschieben. Auf der Chat-Seite (Schluessel vorhanden, window.HKChat.callApi) laeuft hier der
// ganze Anruf; auf anderen Seiten nur das Klingeln - "Annehmen" oeffnet den Anruf im Chat-Tab.
(function () {
  'use strict';
  var HK = window.HK, T = window.T;
  if (!HK || !document.getElementById('hk-user') || document.body.classList.contains('embed')) return;
  var el = HK.el, api = HK.api;
  var CHATS = location.pathname === '/chats';
  var C = null;                                       // laufender Anruf (siehe newCall)
  var RINGS = {};                                     // call_id -> eingehender Dialog {box, d}
  var GONE = {};                                      // call_id -> schon erledigt (abgelehnt/angenommen/vorbei)
  var cfgP = null, cfg = null;
  var mobile = window.matchMedia('(pointer: coarse)').matches;

  function bridge() { var b = window.HKChat && window.HKChat.callApi; return b && b.ready() ? b : null; }
  function config(fresh) {
    if (!cfgP || fresh) cfgP = api('GET', '/api/calls/config').then(function (r) { cfg = r; return r; }, function (e) { cfgP = null; throw e; });
    return cfgP;
  }
  function q(s) { return encodeURIComponent(s); }
  function dur(s) { s = Math.max(0, Math.round(s)); var m = Math.floor(s / 60), h = Math.floor(m / 60); m %= 60; s %= 60; return (h ? h + ':' + (m < 10 ? '0' : '') : '') + m + ':' + (s < 10 ? '0' : '') + s; }
  // Symbole (Linien, currentColor) - statt Emoji, damit sie ueberall gleich aussehen
  var ICONS = {
    mic: ['M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z', 'M19 10v2a7 7 0 0 1-14 0v-2', 'M12 19v3'],
    micOff: ['M2 2l20 20', 'M9 9v3a3 3 0 0 0 5.1 2.1', 'M15 9.3V5a3 3 0 0 0-5.9-.6', 'M17 16.9A7 7 0 0 1 5 12v-2', 'M19 10v2c0 .8-.1 1.5-.4 2.2', 'M12 19v3'],
    cam: ['M23 7l-7 5 7 5V7z', 'M3 5h11a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z'],
    camOff: ['M2 2l20 20', 'M23 7l-7 5 7 5V7z', 'M10.7 5H14a2 2 0 0 1 2 2v3.3', 'M16 16v1a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2'],
    screen: ['M3 4h18a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z', 'M8 21h8', 'M12 17v4', 'M12 13V8', 'M9.5 10.5L12 8l2.5 2.5'],
    phone: ['M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z'],
    x: ['M6 6l12 12', 'M18 6L6 18']
  };
  function icon(name) {
    var NS = 'http://www.w3.org/2000/svg', s = document.createElementNS(NS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '2');
    s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
    ICONS[name].forEach(function (d) { var p = document.createElementNS(NS, 'path'); p.setAttribute('d', d); s.appendChild(p); });
    return s;
  }
  function btn(label, cls, fn, title) {
    var b = el('button', 'cl-btn' + (cls ? ' ' + cls : ''), typeof label === 'string' ? label : null); b.type = 'button';
    if (label && typeof label !== 'string') b.appendChild(label);
    if (title) { b.title = title; b.setAttribute('aria-label', title); }
    if (fn) b.addEventListener('click', fn); return b;
  }

  // --- Klingelton (WebAudio, keine Datei; abschaltbar) ----------------------------------------------------------------
  var AC = null, tone = null;
  function ringOn() { try { return localStorage.getItem('saga-call-ring') !== '0'; } catch (e) { return true; } }
  function audioCtx() {
    if (!AC) { try { AC = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { AC = null; } }
    if (AC && AC.state === 'suspended') AC.resume().catch(function () {});
    return AC;
  }
  // Browser erlauben Ton erst nach einer Bedienung - beim ersten Klick/Tippen vorbereiten
  document.addEventListener('pointerdown', function unlock() { audioCtx(); document.removeEventListener('pointerdown', unlock, true); }, true);
  function beep(ctx, f, t0, len, vol) {
    var o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'sine'; o.frequency.value = f; o.connect(g); g.connect(ctx.destination);
    g.gain.setValueAtTime(0, t0); g.gain.linearRampToValueAtTime(vol, t0 + 0.02); g.gain.setValueAtTime(vol, t0 + len - 0.05); g.gain.linearRampToValueAtTime(0, t0 + len);
    o.start(t0); o.stop(t0 + len + 0.02);
  }
  function playTone(kind) {
    stopTone();
    if (kind === 'ring' && !ringOn()) return;
    var ctx = audioCtx(); if (!ctx) return;
    var step = function () {
      var t0 = ctx.currentTime + 0.05;
      if (kind === 'ring') { beep(ctx, 659, t0, 0.35, 0.18); beep(ctx, 554, t0 + 0.4, 0.5, 0.18); }
      else if (kind === 'back') beep(ctx, 425, t0, 1.0, 0.06);
      else { beep(ctx, 480, t0, 0.25, 0.1); beep(ctx, 380, t0 + 0.3, 0.35, 0.1); }
    };
    step();
    if (kind !== 'end') tone = setInterval(step, kind === 'ring' ? 2600 : 4000);
  }
  function stopTone() { if (tone) { clearInterval(tone); tone = null; } }
  // Mehrere Tabs: nur einer klingelt hoerbar (der sichtbare, sonst der erste)
  var bc = null; try { bc = new BroadcastChannel('saga-call'); } catch (e) { bc = null; }
  var claimed = {};
  if (bc) bc.onmessage = function (e) { var m = e.data || {}; if (m.t === 'claim') { claimed[m.id] = true; if (RINGS[m.id] && RINGS[m.id].loud) { RINGS[m.id].loud = false; stopTone(); } } };
  function ringLoud(id) {
    var go = function () { if (!RINGS[id] || claimed[id]) return; RINGS[id].loud = true; if (bc) bc.postMessage({ t: 'claim', id: id }); playTone('ring'); };
    if (document.visibilityState === 'visible') { claimed[id] = false; go(); } else setTimeout(go, 350);
  }

  // --- Eingehender Anruf: Dialog (auf jeder Seite) -----------------------------------------------------------------
  var titleT = null, title0 = document.title;
  function flashTitle(on, name) {
    clearInterval(titleT); titleT = null;
    if (!on) { document.title = title0; return; }
    title0 = document.title; var n = 0;
    titleT = setInterval(function () { document.title = (n++ % 2) ? title0 : '📞 ' + T('{name} ruft an', { name: name }); }, 1000);
  }
  function ring(d) {
    if (!d || !d.call_id || RINGS[d.call_id] || GONE[d.call_id]) return;
    if (C && C.id !== d.call_id && CHATS && bridge()) {           // telefoniere schon: besetzt
      api('POST', '/api/calls/' + d.call_id + '/decline', { reason: 'busy' }).catch(function () {});
      return;
    }
    var from = d.from || {}, video = d.media === 'video';
    var box = el('div', 'cl-ring'); box.setAttribute('role', 'alertdialog'); box.setAttribute('aria-live', 'assertive');
    box.setAttribute('aria-label', T('Eingehender Anruf von {name}', { name: from.name || '?' }));
    var av = HK.avatarImg(el('span', 'cl-av', (from.name || '?').charAt(0).toUpperCase()), from.avatar, 56); av.setAttribute('aria-hidden', 'true');
    box.appendChild(av);
    var tx = el('div', 'cl-ring-t');
    tx.appendChild(el('b', null, from.name || from.username || '?'));
    tx.appendChild(el('span', null, video ? T('Videoanruf …') : T('Anruf …')));
    box.appendChild(tx);
    var bar = el('div', 'cl-ring-b');
    var snd = btn(ringOn() ? '🔔' : '🔕', 'cl-mini', function () {
      try { localStorage.setItem('saga-call-ring', ringOn() ? '0' : '1'); } catch (e) { /* privat */ }
      snd.textContent = ringOn() ? '🔔' : '🔕'; if (ringOn()) ringLoud(d.call_id); else stopTone();
    }, T('Klingelton an/aus'));
    bar.appendChild(snd);
    bar.appendChild(btn(icon('phone'), 'cl-red', function () { decline(d.call_id); }, T('Ablehnen')));
    if (video) bar.appendChild(btn(icon('phone'), 'cl-green', function () { accept(d, false); }, T('Nur mit Ton annehmen')));
    bar.appendChild(btn(icon(video ? 'cam' : 'phone'), 'cl-green cl-big', function () { accept(d, video); }, video ? T('Mit Video annehmen') : T('Annehmen')));
    box.appendChild(bar);
    document.body.appendChild(box);
    RINGS[d.call_id] = { box: box, d: d, loud: false };
    ringLoud(d.call_id);
    flashTitle(true, from.name || '?');
    var left = Math.max(5, (d.expires_in != null ? d.expires_in : d.ring_s || 45)) * 1000 + 3000;
    RINGS[d.call_id].t = setTimeout(function () { unring(d.call_id); }, left);    // falls das Ende-Ereignis fehlt
    var f = box.querySelector('.cl-big'); if (f && document.visibilityState === 'visible') f.focus({ preventScroll: true });
  }
  function unring(id) {
    GONE[id] = true;
    var r = RINGS[id]; if (!r) return;
    clearTimeout(r.t); r.box.remove(); delete RINGS[id];
    if (r.loud) stopTone();
    if (!Object.keys(RINGS).length) flashTitle(false);
  }
  function decline(id) {
    unring(id);
    api('POST', '/api/calls/' + id + '/decline', { reason: 'declined' }).catch(function () {});
  }
  function accept(d, video) {
    unring(d.call_id);
    if (CHATS && bridge()) { answer(d.call_id, video); return; }
    if (CHATS) { HK.toast(T('Zum Annehmen erst Chats auf diesem Gerät einrichten bzw. entsperren.')); return; }
    // Andere Seite: der Anruf laeuft im Chat-Tab (dort liegen die Schluessel dieses Browsers)
    var w = window.open('/chats#call-' + d.call_id + '-' + (video ? 'video' : 'audio'), 'hk-chats');
    if (w) w.focus(); else location.href = '/chats#call-' + d.call_id + '-' + (video ? 'video' : 'audio');
  }

  // --- Medien ---------------------------------------------------------------------------------------------------
  function mediaError(e, video) {
    var n = e && e.name;
    if (n === 'NotAllowedError' || n === 'SecurityError') return video ? T('Kamera/Mikrofon nicht erlaubt – bitte im Browser (Schloss-Symbol neben der Adresse) für diese Seite erlauben.')
      : T('Mikrofon nicht erlaubt – bitte im Browser (Schloss-Symbol neben der Adresse) für diese Seite erlauben.');
    if (n === 'NotFoundError' || n === 'OverconstrainedError') return video ? T('Keine Kamera oder kein Mikrofon gefunden.') : T('Kein Mikrofon gefunden.');
    if (n === 'NotReadableError' || n === 'AbortError') return T('Kamera oder Mikrofon wird gerade von einem anderen Programm benutzt.');
    if (!navigator.mediaDevices || !window.isSecureContext) return T('Anrufe brauchen eine sichere Verbindung (https).');
    return T('Kamera/Mikrofon nicht verfügbar: {err}', { err: (e && e.message) || n || '?' });
  }
  function getMedia(video) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return Promise.reject(new Error(mediaError(null, video)));
    var a = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    var v = video ? { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' } : false;
    return navigator.mediaDevices.getUserMedia({ audio: a, video: v }).catch(function (e) {
      if (video && (e.name === 'NotFoundError' || e.name === 'NotReadableError' || e.name === 'OverconstrainedError')) {
        HK.toast(T('Keine Kamera verfügbar – Anruf nur mit Ton.'));
        return navigator.mediaDevices.getUserMedia({ audio: a, video: false });
      }
      throw new Error(mediaError(e, video));
    });
  }

  // --- Anruf-Objekt ----------------------------------------------------------------------------------------------
  function newCall(o) {
    return Object.assign({ id: null, chat_id: null, role: 'caller', media: 'audio', peer: {}, peerDevs: {}, peerDevice: null,
      pc: null, local: null, cam: null, screen: null, remote: new MediaStream(), mic: true, seen: {}, early: [], remoteIce: [],
      outIce: [], outT: null, ready: false, connected: false, verified: false, path: '', fps: [], t0: 0, alive: null, statsT: null,
      ending: false, remoteState: { mic: true, cam: false, screen: false }, ui: null }, o);
  }
  function myDev() { return bridge().device(); }
  function X() { return bridge().crypto(); }
  function peerKeys(c, fresh) {
    if (!fresh && c._keys) return c._keys;
    c._keys = bridge().peerKeys(c.peer.username, c.peer.name).then(function (k) {
      c.peerDevs = {}; k.devices.forEach(function (d) { c.peerDevs[d.id] = d; }); return k;
    }, function (e) { c._keys = null; throw e; });
    return c._keys;
  }
  function seal(c, toDev, body) {
    var d = myDev(), pd = c.peerDevs[toDev];
    return X().sealSignal({ call_id: c.id, from_uid: bridge().me(), from_device: d.id, sign_sk: d.sign_sk, to_uid: c.peer.username,
                            to_device: toDev, to_pk: pd.enc_pk, body: body });
  }
  function sealAll(c, devs, body) { var out = {}; devs.forEach(function (id) { out[id] = seal(c, id, body); }); return out; }
  function targets(c) { return c.peerDevice ? [c.peerDevice] : Object.keys(c.peerDevs); }
  // Signal oeffnen + pruefen -> Objekt oder null (wirft nicht). Unbekanntes Geraet: Schluessel einmal neu holen.
  function openSig(c, s) {
    var d = myDev();
    var go = function () {
      var pd = c.peerDevs[s.from_device]; if (!pd) return null;
      var o = X().openSignal(s.sealed, d.enc_pk, d.enc_sk);
      var ok = X().verifySignal({ call_id: c.id, from_uid: c.peer.username, from_device: s.from_device, to_uid: bridge().me(), to_device: d.id, body: o.body }, o.sig, pd.sign_pk);
      if (!ok) return null;
      var b = JSON.parse(o.body); b._from = s.from_device; return b;
    };
    return Promise.resolve().then(function () {
      if (c.peerDevs[s.from_device]) return go();
      return peerKeys(c, true).then(go);
    }).catch(function () { return null; });
  }
  function sendSignal(c, body) {
    if (!c.id || c.ending) return Promise.resolve();
    var devs = targets(c); if (!devs.length) return Promise.resolve();
    return api('POST', '/api/calls/' + c.id + '/signal', { device: myDev().id, signals: sealAll(c, devs, JSON.stringify(body)) }).catch(function (e) {
      if (e.status === 409) remoteEnded(c, { reason: 'gone' });
    });
  }
  function flushIce(c) {
    clearTimeout(c.outT); c.outT = null;
    if (!c.id || !c.ready || !c.outIce.length) return;
    var list = c.outIce.splice(0, 40);
    sendSignal(c, { t: 'ice', c: list });
    if (c.outIce.length) flushIce(c);
  }
  function makePC(c) {
    var pc = new RTCPeerConnection({ iceServers: cfg.ice_servers || [], iceTransportPolicy: cfg.relay_only ? 'relay' : 'all', bundlePolicy: 'max-bundle' });
    pc.onicecandidate = function (e) {
      if (!e.candidate || !e.candidate.candidate) return;
      c.outIce.push(e.candidate.toJSON ? e.candidate.toJSON() : { candidate: e.candidate.candidate, sdpMid: e.candidate.sdpMid, sdpMLineIndex: e.candidate.sdpMLineIndex });
      if (!c.outT) c.outT = setTimeout(function () { flushIce(c); }, 120);
    };
    pc.ontrack = function (e) {
      var t = e.track;
      // ontrack kann mehrfach und in beliebiger Reihenfolge kommen (Ton nach Bild, neue Spur nach Neuverhandlung):
      // beendete Spuren derselben Art raus, neue rein - die Wiedergabe-Elemente bekommen danach eigene Streams (bindRemote)
      c.remote.getTracks().forEach(function (o) { if (o !== t && o.kind === t.kind && o.readyState === 'ended') c.remote.removeTrack(o); });
      if (c.remote.getTracks().indexOf(t) < 0) c.remote.addTrack(t);
      t.onunmute = function () { bindRemote(c); drawMedia(c); };
      t.onmute = function () { drawMedia(c); };
      t.onended = function () { bindRemote(c); drawMedia(c); };
      bindRemote(c);
      drawMedia(c);
    };
    pc.onconnectionstatechange = function () { onConn(c); };
    pc.oniceconnectionstatechange = function () { if (!pc.connectionState) onConn(c); };      // aeltere Browser
    return pc;
  }
  function addLocal(c, pc, fromOffer) {
    var a = c.local.getAudioTracks()[0] || null, v = c.local.getVideoTracks()[0] || null;
    c.cam = v;
    if (a) a.enabled = c.mic;                          // Mikrofon wirklich senden (Stumm-Zustand gilt von Anfang an)
    if (!fromOffer) {
      pc.addTransceiver(a || 'audio', { direction: 'sendrecv', streams: [c.local] });
      pc.addTransceiver(v || 'video', { direction: 'sendrecv', streams: [c.local] });    // immer: Kamera/Bildschirm spaeter ohne neues Angebot
      return;
    }
    // Antwort: Spuren erst fertig einsetzen, dann createAnswer (nicht jeder Browser reiht replaceTrack in die
    // Operationskette ein - sonst kann die Antwort ohne Ton-Spur entstehen)
    var wait = [];
    pc.getTransceivers().forEach(function (tr) {
      var kind = tr.receiver && tr.receiver.track && tr.receiver.track.kind;
      tr.direction = 'sendrecv';
      if (kind === 'audio' && a) wait.push(tr.sender.replaceTrack(a));
      if (kind === 'video') { if (v) wait.push(tr.sender.replaceTrack(v)); tr.sender.setStreams && tr.sender.setStreams(c.local); }
      if (kind === 'audio' && tr.sender.setStreams) tr.sender.setStreams(c.local);
    });
    return Promise.all(wait);
  }
  function videoSender(c) {
    var s = null; c.pc.getTransceivers().forEach(function (tr) { if (!s && tr.receiver.track && tr.receiver.track.kind === 'video') s = tr.sender; });
    return s;
  }
  function applyRemoteIce(c) {
    if (!c.pc || !c.pc.remoteDescription) return;
    var list = c.remoteIce.splice(0);
    list.forEach(function (x) { c.pc.addIceCandidate(x).catch(function () { /* veraltet/ungueltig */ }); });
  }
  function handle(c, b) {
    if (!b) { securityFail(c); return; }
    if (b.t === 'ice') { (Array.isArray(b.c) ? b.c : []).slice(0, 60).forEach(function (x) { if (x && x.candidate) c.remoteIce.push(x); }); applyRemoteIce(c); }
    else if (b.t === 'answer' && c.role === 'caller' && !c.pc.remoteDescription) {
      if (c.peerDevice && b._from !== c.peerDevice) return;
      if (!fpOk(b)) { securityFail(c); return; }
      c.peerDevice = b._from;                          // ab jetzt Signale nur noch an das annehmende Geraet
      c.fps = X().sdpFingerprints(b.sdp);
      c.pc.setRemoteDescription({ type: 'answer', sdp: b.sdp }).then(function () { applyRemoteIce(c); }).catch(function (e) { fail(c, T('Verbindung nicht aufgebaut: {err}', { err: e.message })); });
    } else if (b.t === 'state') {
      c.remoteState = { mic: b.mic !== false, cam: !!b.cam, screen: !!b.screen }; c.gotState = true; drawMedia(c);
    }
  }
  function fpOk(b) {
    var fp = X().sdpFingerprints(b.sdp);
    return fp.length > 0 && JSON.stringify(fp) === JSON.stringify((b.fp || []).slice().sort());
  }
  function signalIn(d) {
    var c = C;
    if (!c || d.call_id !== c.id || !bridge() || d.to_device !== myDev().id || c.seen[d.seq]) return;
    c.seen[d.seq] = true;
    if (!c.ready) { c.early.push(d); return; }
    openSig(c, d).then(function (b) { if (C === c) handle(c, b); });
  }

  // --- Anrufen -----------------------------------------------------------------------------------------------------
  function start(chat, video) {
    var b = bridge();
    if (!b) { HK.toast(T('Chats sind auf diesem Gerät noch nicht bereit.')); return; }
    if (C) { HK.toast(T('Du telefonierst gerade schon.')); return; }
    if (!chat || chat.kind !== 'dm' || !chat.peer) return;
    if (!window.RTCPeerConnection) { HK.toast(T('Dieser Browser kann keine Anrufe (WebRTC fehlt).')); return; }
    var c = C = newCall({ chat_id: chat.id, role: 'caller', media: video ? 'video' : 'audio', peer: chat.peer });
    audioCtx();
    win(c); status(c, T('Kamera und Mikrofon …'));
    config(true).then(function () {
      if (!cfg.enabled) throw new Error(T('Anrufe sind auf diesem Server abgeschaltet.'));
      return peerKeys(c);
    }).then(function (k) {
      if (!k.devices.length) throw new Error(T('{name} hat Chats noch auf keinem Gerät eingerichtet – Anrufe gehen erst danach.', { name: c.peer.name }));
      return getMedia(video);
    }).then(function (s) {
      if (C !== c) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
      c.local = s; c.media = s.getVideoTracks().length ? 'video' : 'audio'; drawMedia(c); sinkPick(c);
      c.pc = makePC(c); addLocal(c, c.pc, false);
      return c.pc.createOffer().then(function (o) { return c.pc.setLocalDescription(o); }).then(function () { return dial(c, false); });
    }).catch(function (e) { if (C === c) fail(c, e.message); });
  }
  function dial(c, again) {
    status(c, T('Verbinde …'));
    c.id = c.id || X().hex(16);
    var sdp = c.pc.localDescription.sdp, body = JSON.stringify({ t: 'offer', sdp: sdp, media: c.media, fp: X().sdpFingerprints(sdp) });
    return api('POST', '/api/calls', { id: c.id, chat_id: c.chat_id, media: c.media, device: myDev().id, signals: sealAll(c, Object.keys(c.peerDevs), body) })
      .then(function (r) {
        if (C !== c) { api('POST', '/api/calls/' + c.id + '/end', { reason: 'hangup' }).catch(function () {}); return; }
        c.ready = true; c.call = r.call;
        status(c, T('Klingelt …')); playTone('back');
        flushIce(c); aliveOn(c);
      }, function (e) {
        if (e.status === 409 && e.data && e.data.devices && !again) return peerKeys(c, true).then(function () { return dial(c, true); });
        if (e.status === 409 && e.data && e.data.busy) { c.id = null; finish(c, T('Besetzt – {name} telefoniert gerade.', { name: c.peer.name }), 'busy'); return; }
        c.id = null; throw e;
      });
  }

  // --- Annehmen (Chat-Seite) ---------------------------------------------------------------------------------------
  function answer(id, video) {
    unring(id);
    if (C) { HK.toast(T('Du telefonierst gerade schon.')); return; }
    var c = C = newCall({ id: id, role: 'callee' });
    audioCtx();
    win(c); status(c, T('Verbinde …'));
    var dev = myDev().id, offer = null;
    config(true).then(function () {
      return api('GET', '/api/calls/' + id + '?device=' + q(dev));
    }).then(function (r) {
      c.call = r.call; c.chat_id = r.call.chat_id; c.media = r.call.media; c.peer = r.call.peer; drawPeer(c);
      if (r.call.role !== 'callee' || r.call.state !== 'ringing') throw new Error(r.call.state === 'active' ? T('Der Anruf wurde auf einem anderen Gerät angenommen.') : T('Der Anruf ist schon beendet.'));
      r.signals.forEach(function (s) { c.seen[s.seq] = true; });
      return peerKeys(c).then(function () {
        return Promise.all(r.signals.map(function (s) { return openSig(c, s); }));
      });
    }).then(function (list) {
      if (list.some(function (b) { return !b; })) { securityFail(c); throw null; }
      list.forEach(function (b) {
        if (b._from !== c.call.caller_device) return;
        if (b.t === 'offer' && !offer) offer = b;
        else if (b.t === 'ice') (b.c || []).forEach(function (x) { if (x && x.candidate) c.remoteIce.push(x); });
      });
      if (!offer || !fpOk(offer)) { securityFail(c); throw null; }
      c.peerDevice = c.call.caller_device; c.fps = X().sdpFingerprints(offer.sdp);
      return getMedia(video);
    }).then(function (s) {
      if (C !== c) { s.getTracks().forEach(function (t) { t.stop(); }); throw null; }
      c.local = s; drawMedia(c); sinkPick(c);
      c.pc = makePC(c);
      return c.pc.setRemoteDescription({ type: 'offer', sdp: offer.sdp });
    }).then(function () {
      return addLocal(c, c.pc, true);
    }).then(function () {
      return c.pc.createAnswer();
    }).then(function (a) { return c.pc.setLocalDescription(a); }).then(function () {
      var sdp = c.pc.localDescription.sdp, body = JSON.stringify({ t: 'answer', sdp: sdp, fp: X().sdpFingerprints(sdp) });
      var sig = {}; sig[c.peerDevice] = seal(c, c.peerDevice, body);
      return api('POST', '/api/calls/' + id + '/answer', { device: dev, signals: sig });
    }).then(function () {
      if (C !== c) return;
      c.ready = true; c.t0 = 0;
      status(c, T('Verbinde …'));
      applyRemoteIce(c); flushIce(c); aliveOn(c); stateOut(c);
      var early = c.early.splice(0);
      early.forEach(function (d) { openSig(c, d).then(function (b) { if (C === c) handle(c, b); }); });
      c.connT = setTimeout(function () { if (C === c && !c.connected) iceFailed(c); }, 30000);
    }).catch(function (e) {
      if (C !== c || e === null) return;
      fail(c, e.status === 409 ? T('Der Anruf ist schon beendet oder wurde auf einem anderen Gerät angenommen.') : e.message);
    });
  }

  // --- Verbindung, Pruefung, Qualitaet ---------------------------------------------------------------------------------
  function onConn(c) {
    if (C !== c || !c.pc) return;
    var st = c.pc.connectionState || c.pc.iceConnectionState;
    if (st === 'connected' || st === 'completed') {
      if (!c.connected) { c.connected = true; c.t0 = Date.now(); stopTone(); clearTimeout(c.connT); checkPath(c); stateOut(c); }
      status(c, ''); tick(c);
    } else if (st === 'disconnected') status(c, T('Verbindung unterbrochen – versuche es weiter …'));
    else if (st === 'failed') iceFailed(c);
  }
  function iceFailed(c) {
    fail(c, cfg && cfg.turn ? T('Keine Verbindung möglich – auch nicht über den TURN-Server. Bitte später erneut versuchen.')
      : T('Direktverbindung nicht möglich (z. B. strenge Firewall oder Mobilfunknetz). Der Admin kann einen TURN-Server eintragen.'), 'failed');
  }
  // Nach dem Verbinden: DTLS-Fingerabdruck des Gegenuebers (getStats) muss zur SIGNIERTEN SDP passen; Weg (direkt/Relais)
  function checkPath(c) {
    c.pc.getStats().then(function (rep) {
      var tr = null, pair = null, certFp = null, byId = {};
      rep.forEach(function (s) { byId[s.id] = s; if (s.type === 'transport' && !tr) tr = s; });
      if (tr && tr.remoteCertificateId && byId[tr.remoteCertificateId]) {
        var cert = byId[tr.remoteCertificateId];
        certFp = (cert.fingerprintAlgorithm || '').toLowerCase() + ' ' + String(cert.fingerprint || '').toUpperCase();
      }
      if (certFp && c.fps.length && c.fps.indexOf(certFp) < 0) { securityFail(c); return; }
      c.verified = !!certFp || c.fps.length > 0;
      if (tr && tr.selectedCandidatePairId) pair = byId[tr.selectedCandidatePairId];
      if (!pair) rep.forEach(function (s) { if (s.type === 'candidate-pair' && (s.selected || s.nominated) && s.state === 'succeeded' && !pair) pair = s; });
      if (pair) {
        var l = byId[pair.localCandidateId], r = byId[pair.remoteCandidateId];
        c.path = (l && l.candidateType === 'relay') || (r && r.candidateType === 'relay') ? 'relay' : 'direct';
      }
      drawInfo(c);
    }).catch(function () {});
  }
  function quality(c) {
    if (!c.pc) return;
    c.pc.getStats().then(function (rep) {
      var lost = 0, recv = 0, rtt = null, bytes = 0, ai = null, ao = null;
      rep.forEach(function (s) {
        if (s.type === 'inbound-rtp') { lost += s.packetsLost || 0; recv += s.packetsReceived || 0; bytes += s.bytesReceived || 0; }
        if (s.type === 'inbound-rtp' && (s.kind || s.mediaType) === 'audio') ai = s;
        if (s.type === 'outbound-rtp' && (s.kind || s.mediaType) === 'audio') ao = s;
        if (s.type === 'candidate-pair' && (s.nominated || s.selected) && s.currentRoundTripTime != null) rtt = s.currentRoundTripTime;
      });
      soundStats(c, ai, ao);
      var d = (c._q || { lost: 0, recv: 0 }), dl = lost - d.lost, dr = recv - d.recv;
      c._q = { lost: lost, recv: recv };
      var loss = dr + dl > 0 ? dl / (dr + dl) : 0;
      c.q = rtt == null && !dr ? '' : (loss > 0.08 || rtt > 0.6) ? 'bad' : (loss > 0.02 || rtt > 0.3) ? 'mid' : 'good';
      c.bytesIn = bytes;
      drawInfo(c);
    }).catch(function () {});
  }
  // Ton-Diagnose aus getStats: kommen Audio-Pakete an, spielt das <audio>-Element sie ab, geht eigener Ton raus?
  function soundStats(c, ai, ao) {
    var pk = ai ? ai.packetsReceived || 0 : 0, prev = c._snd || { pk: 0, idle: 0 }, fresh = pk > prev.pk;
    c._snd = { pk: pk, idle: fresh ? 0 : prev.idle + 1 };
    var ra = c.ui && c.ui.ra;
    c.audio = { packetsIn: pk, bytesIn: ai ? ai.bytesReceived || 0 : 0, samplesIn: ai ? ai.totalSamplesReceived || 0 : 0,
                levelIn: ai && ai.audioLevel != null ? ai.audioLevel : null, bytesOut: ao ? ao.bytesSent || 0 : 0,
                playing: !!(ra && ra.srcObject && !ra.paused) };
    if (fresh && ra && ra.paused && !c.audioBlocked) { bindRemote(c); playAudio(c); }
    c.snd = !c.connected ? '' : c.audioBlocked ? 'blocked' : fresh && c.audio.playing ? 'ok' : c._snd.idle >= 2 || (fresh && !ra) ? 'none' : c.snd || '';
  }
  // Wiedergabe: Ton ueber das eigene <audio> (nur Audiospuren), Bild stumm im <video> (nur Videospuren). Bei jeder
  // Aenderung der Spuren ein frischer MediaStream - einem schon gesetzten Stream spaeter hinzugefuegte Spuren spielt
  // Safari nicht zuverlaessig ab (Ton kommt oft nach dem Bild an).
  function bindRemote(c) {
    if (!c.ui) return;
    var live = function (t) { return t.readyState === 'live'; };
    [[c.ui.ra, c.remote.getAudioTracks().filter(live)], [c.ui.rv, c.remote.getVideoTracks().filter(live)]].forEach(function (x) {
      var m = x[0], want = x[1], have = m.srcObject ? m.srcObject.getTracks() : [];
      if (want.length === have.length && want.every(function (t) { return have.indexOf(t) >= 0; })) return;
      m.srcObject = want.length ? new MediaStream(want) : null;
    });
    playAudio(c);
  }
  // play() kann an der Autoplay-Regel scheitern (Anruf im neu geoeffneten Tab, Safari ohne Bedienung): dann Knopf
  // "Ton einschalten" - jede Bedienung im Fenster versucht es erneut
  function playAudio(c) {
    var ra = c.ui && c.ui.ra;
    if (ra && !ra.paused && c.audioBlocked) { c.audioBlocked = false; c.snd = ''; drawInfo(c); }
    if (!ra || !ra.srcObject || !ra.paused || c.done) return;
    var p; try { p = ra.play(); } catch (e) { p = null; }
    if (p && p.then) p.then(function () {
      if (c.audioBlocked) { c.audioBlocked = false; c.snd = ''; drawInfo(c); }
    }, function (e) {
      if (e && e.name === 'NotAllowedError' && !c.done && ra.paused) { c.audioBlocked = true; c.snd = 'blocked'; drawInfo(c); }
    });
  }
  // Im Klick "Anrufen"/"Annehmen" (Nutzer-Geste) das <audio> einmal starten - Safari (iOS/macOS) erlaubt das spaetere
  // play() nach den asynchronen Schritten (Schluessel, getUserMedia, SDP) sonst nicht
  function unlockAudio(c) {
    var ra = c.ui && c.ui.ra; if (!ra) return;
    try { var p = ra.play(); if (p && p.catch) p.catch(function () {}); } catch (e) { /* egal */ }
  }
  document.addEventListener('pointerdown', function () { if (C && C.audioBlocked) playAudio(C); }, true);
  document.addEventListener('keydown', function () { if (C && C.audioBlocked) playAudio(C); }, true);
  // Ausgabegeraet (nur wo der Browser es kann und es mehrere gibt): Auswahl merken
  function sinkPick(c) {
    var ra = c.ui && c.ui.ra;
    if (!ra || typeof ra.setSinkId !== 'function' || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    navigator.mediaDevices.enumerateDevices().then(function (list) {
      var outs = list.filter(function (d) { return d.kind === 'audiooutput' && d.deviceId && d.deviceId !== 'communications'; });
      if (!c.ui || outs.length < 2 || c.ui.sink) return;
      var want = ''; try { want = localStorage.getItem('saga-call-sink') || ''; } catch (e) { /* privat */ }
      var sel = el('select', 'cl-sink'); sel.title = T('Tonausgabe'); sel.setAttribute('aria-label', T('Tonausgabe'));
      outs.forEach(function (d, i) { var o = el('option', null, d.label || T('Ausgabe {n}', { n: i + 1 })); o.value = d.deviceId; sel.appendChild(o); });
      sel.value = outs.some(function (d) { return d.deviceId === want; }) ? want : outs[0].deviceId;
      var apply = function () {
        ra.setSinkId(sel.value === 'default' ? '' : sel.value).catch(function (e) { HK.toast(T('Tonausgabe nicht umschaltbar: {err}', { err: e.message })); });
      };
      sel.addEventListener('change', function () { try { localStorage.setItem('saga-call-sink', sel.value); } catch (e) { /* privat */ } apply(); });
      if (want && sel.value === want && want !== 'default') apply();
      c.ui.sink = sel; c.ui.info.parentNode.insertBefore(sel, c.ui.info.nextSibling);
    }).catch(function () {});
  }
  function tick(c) {
    clearInterval(c.statsT);
    c.statsT = setInterval(function () { if (C !== c) return clearInterval(c.statsT); quality(c); drawInfo(c); if (!c.path) checkPath(c); }, 2000);
  }
  function aliveOn(c) {
    clearInterval(c.alive);
    c.alive = setInterval(function () {
      if (C !== c || !c.id) return clearInterval(c.alive);
      api('POST', '/api/calls/' + c.id + '/alive', {}).then(function (r) { if (r.state === 'ended') remoteEnded(c, { reason: r.reason }); }).catch(function () {});
    }, ((cfg && cfg.alive_s) || 15) * 1000);
  }

  // --- Bedienung -------------------------------------------------------------------------------------------------
  function stateOut(c) { if (c.ready) sendSignal(c, { t: 'state', mic: c.mic, cam: !!(c.cam && c.cam.readyState === 'live' && c.cam.enabled), screen: !!c.screen }); }
  function toggleMic(c) {
    c.mic = !c.mic;
    (c.local ? c.local.getAudioTracks() : []).forEach(function (t) { t.enabled = c.mic; });
    drawCtl(c); stateOut(c);
  }
  function toggleCam(c) {
    var snd = c.pc && videoSender(c);
    if (c.cam) {
      c.cam.stop(); if (c.local) c.local.removeTrack(c.cam); c.cam = null;
      if (snd && !c.screen) snd.replaceTrack(null);
      drawMedia(c); drawCtl(c); stateOut(c); return;
    }
    navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' } }).then(function (s) {
      if (C !== c) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
      c.cam = s.getVideoTracks()[0]; c.local.addTrack(c.cam);
      if (snd && !c.screen) snd.replaceTrack(c.cam);
      drawMedia(c); drawCtl(c); stateOut(c);
    }).catch(function (e) { HK.toast(mediaError(e, true)); });
  }
  function toggleScreen(c) {
    var snd = c.pc && videoSender(c); if (!snd) return;
    if (c.screen) { stopScreen(c); return; }
    navigator.mediaDevices.getDisplayMedia({ video: true, audio: false }).then(function (s) {
      if (C !== c) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
      c.screen = s.getVideoTracks()[0];
      c.screen.onended = function () { if (C === c && c.screen) stopScreen(c); };
      snd.replaceTrack(c.screen);
      drawMedia(c); drawCtl(c); stateOut(c);
    }).catch(function (e) { if (e && e.name !== 'NotAllowedError' && e.name !== 'AbortError') HK.toast(T('Bildschirm teilen nicht möglich: {err}', { err: e.message })); });
  }
  function stopScreen(c) {
    var snd = c.pc && videoSender(c);
    if (c.screen) { c.screen.onended = null; c.screen.stop(); c.screen = null; }
    if (snd) snd.replaceTrack(c.cam || null);
    drawMedia(c); drawCtl(c); stateOut(c);
  }
  function hangup(c) {
    if (C !== c) return;
    if (c.id && !c.ending) api('POST', '/api/calls/' + c.id + '/end', { reason: 'hangup', connected: c.connected }).catch(function () {});
    finish(c, c.connected ? T('Anruf beendet · {d}', { d: dur((Date.now() - c.t0) / 1000) }) : T('Anruf beendet'));
  }
  function fail(c, msg, reason) {
    if (C !== c) return;
    if (c.id && !c.ending) api('POST', '/api/calls/' + c.id + '/end', { reason: reason === 'failed' ? 'failed' : 'hangup', connected: c.connected }).catch(function () {});
    finish(c, msg || T('Anruf beendet'), 'error');
  }
  function securityFail(c) {
    fail(c, T('Sicherheitsprüfung fehlgeschlagen: Die Signatur bzw. der Schlüssel der Gegenseite passt nicht. Der Anruf wurde abgebrochen – möglicherweise versucht jemand mitzuhören. Prüfe die Sicherheitsnummer im Chat.'), 'failed');
  }
  function remoteEnded(c, d) {
    if (C !== c) return;
    var r = d.reason, name = c.peer.name || '';
    var msg = r === 'declined' ? (c.role === 'caller' ? T('{name} hat abgelehnt.', { name: name }) : T('Abgelehnt.'))
      : r === 'busy' ? T('Besetzt – {name} telefoniert gerade.', { name: name })
      : r === 'timeout' ? (c.role === 'caller' ? T('{name} hat nicht abgenommen.', { name: name }) : T('Verpasster Anruf.'))
      : r === 'cancelled' ? T('{name} hat aufgelegt.', { name: name })
      : r === 'lost' ? T('Verbindung verloren.')
      : r === 'failed' ? T('Die Verbindung kam nicht zustande.')
      : c.connected ? T('Anruf beendet · {d}', { d: dur((Date.now() - c.t0) / 1000) }) : T('Anruf beendet');
    c.ending = true;
    finish(c, msg, r === 'hangup' || !r ? '' : 'info');
  }
  function finish(c, msg, kind) {
    if (c.done) return;
    c.done = true; c.ending = true;
    clearInterval(c.alive); clearInterval(c.statsT); clearTimeout(c.outT); clearTimeout(c.connT);
    stopTone(); if (kind !== 'error' && c.ready) playTone('end');
    [c.local, c.screen && { getTracks: function () { return [c.screen]; } }].forEach(function (s) { if (s) s.getTracks().forEach(function (t) { t.stop(); }); });
    if (c.cam) c.cam.stop();
    try { if (c.pc) c.pc.close(); } catch (e) { /* schon zu */ }
    if (C === c) C = null;
    if (c.ui) {
      status(c, msg || '', kind === 'error');
      c.ui.ctl.textContent = '';
      c.ui.ctl.appendChild(btn(T('Schließen'), 'cl-pill', function () { closeWin(c); }));
      if (kind !== 'error') setTimeout(function () { closeWin(c); }, 3500);
    } else if (msg) HK.toast(msg);
    if (location.hash.indexOf('#call-') === 0) history.replaceState(null, '', location.pathname);
    if (window.HKChat && window.HKChat.callApi && c.chat_id) window.HKChat.callApi.refresh(c.chat_id);
  }

  // --- Anruffenster --------------------------------------------------------------------------------------------------
  function win(c) {
    var w = el('div', 'cl-win'); w.setAttribute('role', 'dialog'); w.setAttribute('aria-modal', 'false'); w.setAttribute('aria-label', T('Anruf'));
    var stage = el('div', 'cl-stage');
    var rv = el('video', 'cl-remote'); rv.autoplay = true; rv.muted = true; rv.playsInline = true; rv.setAttribute('playsinline', '');   // Ton: <audio>
    var ra = el('audio', 'cl-raudio'); ra.autoplay = true;
    ra.addEventListener('playing', function () { if (c.audioBlocked) { c.audioBlocked = false; c.snd = ''; drawInfo(c); } });
    var who = el('div', 'cl-who'), lv = el('video', 'cl-local'); lv.hidden = true; lv.autoplay = true; lv.muted = true; lv.playsInline = true; lv.setAttribute('playsinline', '');
    stage.appendChild(rv); stage.appendChild(ra); stage.appendChild(who); stage.appendChild(lv);
    var top = el('div', 'cl-top'), info = el('div', 'cl-info'), st = el('div', 'cl-status'); st.setAttribute('role', 'status');
    var mini = btn('▭', 'cl-mini cl-shrink', function () { w.classList.toggle('small'); }, T('Kleiner/größer'));
    top.appendChild(info); top.appendChild(mini);
    var ctl = el('div', 'cl-ctl');
    var un = btn(T('Ton einschalten'), 'cl-pill cl-unmute', function () { playAudio(c); }, T('Der Browser hat den Ton blockiert – tippen, um ihn einzuschalten'));
    un.hidden = true;
    w.appendChild(stage); w.appendChild(top); w.appendChild(un); w.appendChild(st); w.appendChild(ctl);
    document.body.appendChild(w);
    c.ui = { w: w, rv: rv, ra: ra, lv: lv, who: who, info: info, st: st, ctl: ctl, un: un };
    unlockAudio(c);                                    // laeuft im Klick "Anrufen"/"Annehmen"
    bindRemote(c);
    drawPeer(c); drawCtl(c); drawInfo(c);
  }
  function closeWin(c) { if (c.ui) { c.ui.w.remove(); c.ui = null; } }
  function drawPeer(c) {
    if (!c.ui) return;
    var p = c.peer || {}, who = c.ui.who; who.textContent = '';
    var av = HK.avatarImg(el('span', 'cl-av cl-av-big', (p.name || '?').charAt(0).toUpperCase()), p.avatar, 96); av.setAttribute('aria-hidden', 'true');
    who.appendChild(av); who.appendChild(el('div', 'cl-name', p.name || p.username || ''));
  }
  function status(c, s, err) { if (c.ui) { c.ui.st.textContent = s || ''; c.ui.st.classList.toggle('err', !!err); c.ui.st.hidden = !s; } }
  function drawInfo(c) {
    if (!c.ui) return;
    var i = c.ui.info; i.textContent = '';
    if (!c.ready && !c.connected) return;
    i.appendChild(el('span', 'cl-lock', '🔒 ' + (c.verified ? T('Ende-zu-Ende verschlüsselt') : T('verschlüsselt'))));
    if (c.verified) i.lastChild.title = T('Medien mit DTLS-SRTP verschlüsselt; der Schlüssel-Fingerabdruck ist mit dem Chat-Schlüssel der Gegenseite signiert und geprüft.');
    if (c.connected) {
      i.appendChild(el('span', null, dur((Date.now() - c.t0) / 1000)));
      if (c.path) i.appendChild(el('span', 'cl-path', c.path === 'relay' ? T('über Relais (TURN)') : T('direkt')));
      if (c.q) { var qq = el('span', 'cl-q cl-q-' + c.q, c.q === 'good' ? T('gute Verbindung') : c.q === 'mid' ? T('mittlere Verbindung') : T('schwache Verbindung')); i.appendChild(qq); }
      if (c.snd === 'ok') i.appendChild(el('span', 'cl-snd', T('Ton: empfangen')));
      else if (c.snd === 'none' || c.snd === 'blocked') {
        var sn = el('span', 'cl-snd cl-snd-no', c.snd === 'blocked' ? T('Ton: blockiert') : T('Ton: kein Ton'));
        sn.title = c.snd === 'blocked' ? T('Der Browser hat den Ton blockiert – tippen, um ihn einzuschalten') : T('Vom Gegenüber kommt kein Ton an.');
        i.appendChild(sn);
      }
    }
    if (c.ui.un) c.ui.un.hidden = !c.audioBlocked;
  }
  function drawMedia(c) {
    if (!c.ui) return;
    var rvid = c.remote.getVideoTracks().filter(function (t) { return t.readyState === 'live' && !t.muted; }).length > 0;
    var show = rvid && (c.gotState ? c.remoteState.cam || c.remoteState.screen : true);
    c.ui.w.classList.toggle('has-video', show);
    c.ui.w.classList.toggle('peer-muted', c.remoteState.mic === false);
    var mine = c.screen || c.cam;
    if (mine) { var s = new MediaStream([mine]); if (!c.ui.lv.srcObject || c.ui.lv.srcObject.getTracks()[0] !== mine) c.ui.lv.srcObject = s; }
    else c.ui.lv.srcObject = null;
    c.ui.lv.hidden = !mine;
    c.ui.w.classList.toggle('screen', !!c.screen);
    if (show && c.ui.rv.srcObject && c.ui.rv.paused) c.ui.rv.play().catch(function () {});
    playAudio(c);
  }
  function drawCtl(c) {
    if (!c.ui || c.done) return;
    var x = c.ui.ctl; x.textContent = '';
    var m = btn(icon(c.mic ? 'mic' : 'micOff'), 'cl-round' + (c.mic ? '' : ' off'), function () { toggleMic(c); }, c.mic ? T('Stummschalten') : T('Mikrofon einschalten'));
    m.setAttribute('aria-pressed', String(!c.mic)); x.appendChild(m);
    var v = btn(icon(c.cam ? 'cam' : 'camOff'), 'cl-round' + (c.cam ? '' : ' off'), function () { if (c.local) toggleCam(c); }, c.cam ? T('Kamera aus') : T('Kamera an'));
    v.setAttribute('aria-pressed', String(!c.cam)); x.appendChild(v);
    if (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia && !mobile) {
      var s = btn(icon('screen'), 'cl-round' + (c.screen ? ' on' : ''), function () { if (c.pc) toggleScreen(c); }, c.screen ? T('Teilen beenden') : T('Bildschirm teilen'));
      s.setAttribute('aria-pressed', String(!!c.screen)); x.appendChild(s);
    }
    x.appendChild(btn(icon('phone'), 'cl-round cl-red', function () { hangup(c); }, T('Auflegen')));
  }

  // --- Live-Ereignisse ---------------------------------------------------------------------------------------------
  document.addEventListener('hk-live', function (e) {
    var t = e.detail.type, d = e.detail.data || {};
    if (t === 'call.ring') {
      // nachgeholte Ereignisse (Last-Event-ID) koennen alt sein: Zustand pruefen, bevor es klingelt
      api('GET', '/api/calls/' + d.call_id).then(function (r) {
        if (r.call.state === 'ringing' && r.call.role === 'callee') ring(Object.assign({}, d, { expires_in: r.call.expires_in }));
      }).catch(function () {});
    } else if (t === 'call.signal') signalIn(d);
    else if (t === 'call.state') {
      if (d.state === 'active' || d.state === 'ended') unring(d.call_id);
      var c = C;
      if (c && c.id === d.call_id) {
        if (d.state === 'ended') remoteEnded(c, d);
        else if (d.state === 'active' && c.role === 'caller') {
          c.peerDevice = d.device; stopTone(); status(c, T('Verbinde …'));
          c.connT = setTimeout(function () { if (C === c && !c.connected) iceFailed(c); }, 30000);
        } else if (d.state === 'active' && c.role === 'callee' && !c.ready && d.device !== myDev().id) {
          finish(c, T('Der Anruf wurde auf einem anderen Gerät angenommen.'), 'info');
        }
      }
    } else if (t === 'resync') pending();
  });
  // Seite neu geladen/geoeffnet, waehrend es klingelt
  function pending() {
    api('GET', '/api/calls/active').then(function (r) {
      (r.calls || []).forEach(function (x) {
        if (x.role === 'callee' && x.state === 'ringing' && !RINGS[x.id] && !(C && C.id === x.id))
          ring({ call_id: x.id, chat_id: x.chat_id, media: x.media, from: x.peer, ring_s: x.ring_s, expires_in: x.expires_in });
      });
    }).catch(function () {});
  }
  // Ohne Live-Kanal (Proxy puffert SSE o. Ae.): Klingeln und Signale abfragen
  function sse() { return !!(window.HKLive && window.HKLive.state && window.HKLive.state().sse); }
  setInterval(function () {
    if (sse()) return;
    var c = C;
    if (c && c.id && c.ready && !c.done && bridge()) {
      var after = Math.max.apply(null, [0].concat(Object.keys(c.seen).map(Number)));
      api('GET', '/api/calls/' + c.id + '?device=' + q(myDev().id) + '&after=' + after).then(function (r) {
        (r.signals || []).forEach(function (x) { signalIn(Object.assign({ call_id: c.id }, x)); });
        if (r.call.state === 'ended') remoteEnded(c, { reason: r.call.reason });
        else if (r.call.state === 'active' && c.role === 'caller' && !c.peerDevice) c.peerDevice = r.call.callee_device;
      }).catch(function () {});
    } else if (!document.hidden && Date.now() % 10000 < 3000) pending();
  }, 3000);
  window.addEventListener('beforeunload', function (e) { if (C && C.ready && !C.done) { e.preventDefault(); e.returnValue = ''; } });
  window.addEventListener('pagehide', function () { if (C && C.id && !C.done) api('POST', '/api/calls/' + C.id + '/end', { reason: 'hangup', connected: C.connected }).catch(function () {}); });

  // Chat-Seite: #call-<id>[-audio|-video] (aus einer Mitteilung oder einem anderen Tab) -> annehmen bzw. klingeln lassen
  function route() {
    var m = /^#call-([0-9a-f]{32})(?:-(audio|video))?$/.exec(location.hash);
    if (!m) return;
    var n = 0, go = function () {
      if (!bridge()) { if (++n < 80) return setTimeout(go, 250); HK.toast(T('Zum Annehmen erst Chats auf diesem Gerät einrichten bzw. entsperren.')); return; }
      if (C && C.id === m[1]) return;
      if (m[2]) answer(m[1], m[2] === 'video');
      else api('GET', '/api/calls/' + m[1]).then(function (r) {
        if (r.call.state === 'ringing' && r.call.role === 'callee') ring({ call_id: r.call.id, chat_id: r.call.chat_id, media: r.call.media, from: r.call.peer, expires_in: r.call.expires_in });
        else { HK.toast(T('Der Anruf ist schon beendet.')); history.replaceState(null, '', location.pathname); }
      }).catch(function () {});
    };
    go();
  }
  if (CHATS) { window.addEventListener('hashchange', route); route(); }

  // Knoepfe im Kopf des Direkt-Chats (chat.js ruft head(chat) beim Zeichnen)
  function head(chat) {
    var a = document.getElementById('ct-call-a'), v = document.getElementById('ct-call-v');
    if (!a || !v) return;
    var on = !!(chat && chat.kind === 'dm' && cfg && cfg.enabled && window.RTCPeerConnection);
    a.hidden = v.hidden = !on;
  }
  if (CHATS) {
    var a0 = document.getElementById('ct-call-a'), v0 = document.getElementById('ct-call-v');
    var cur = function () { return window.HKChat && window.HKChat.callApi && window.HKChat.callApi.cur(); };
    if (a0) a0.addEventListener('click', function () { start(cur(), false); });
    if (v0) v0.addEventListener('click', function () { start(cur(), true); });
    config().then(function () { head(cur()); }).catch(function () {});
  }
  setTimeout(pending, CHATS ? 1500 : 800);

  window.HKCalls = { head: head, start: start, state: function () {
    return C ? { id: C.id, role: C.role, connected: C.connected, verified: C.verified, path: C.path, bytesIn: C.bytesIn || 0,
                 mic: C.mic, cam: !!C.cam, screen: !!C.screen, remote: C.remoteState,
                 audio: Object.assign({ state: C.snd || '', blocked: !!C.audioBlocked }, C.audio || {}) } : null;
  }, ringing: function () { return Object.keys(RINGS); } };
})();
