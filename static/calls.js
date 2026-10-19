// Saga - Anrufe (2.1): 1:1-Audio/Video aus dem Direkt-Chat, WebRTC zwischen den Geraeten, ohne zusaetzlichen Port.
// Signalisierung ueber den Live-Kanal (live.js -> Ereignis 'hk-live': call.ring / call.signal / call.state) und POSTs an
// /api/calls. Jede Signal-Nachricht (SDP, ICE) wird mit dem Chat-Geraeteschluessel signiert und fuer das Zielgeraet
// versiegelt (static/chat-crypto.js, Format saga-call/v1) - der Server sieht weder SDP noch IP-Adressen und kann keinen
// eigenen DTLS-Schluessel unterschieben. Auf der Chat-Seite (Schluessel vorhanden, window.HKChat.callApi) laeuft hier der
// ganze Anruf; auf anderen Seiten nur das Klingeln - "Annehmen" oeffnet den Anruf im Chat-Tab.
// 2.4 (E0, "saga-call v3", Vertrag docs/APP-API.md): Body-Felder pc/caps/n, Neuverhandlung waehrend des Gespraechs nach
// "Perfect Negotiation" (hoeflich = groessere device_id), ICE-Restart bei Abbruch/Netzwechsel, state mit pip/want/path,
// bye. Neuverhandlung und want nur, wenn das Gegenueber sie in caps meldet - 2.1/2.3-Gegenstellen verhalten sich wie
// bisher. Bitrate je Weg (direkt / Relais UDP / Relais TCP) und je Wunsch des Gegenuebers (want small, z. B. Bild-im-Bild)
// ueber RTCRtpSender.setParameters. Bild-im-Bild: Document-PiP (eigene Knoepfe) bzw. Video-PiP, wo der Browser es kann.
// 2.4 (E3, "Bildschirm v2"): Bildschirm als eigene, zweite Videospur (Betriebsart B, eigener Transceiver mit contentHint
// "detail", Neuverhandlung), wenn beide Seiten reneg und screen2 melden - die Kamera laeuft daneben gedrosselt weiter und
// das Gegenueber zeigt Bildschirm gross, Kamera klein (umschaltbar). Sonst wie 2.3 replaceTrack auf dem Kamera-Sender
// (Betriebsart A). Systemton (wo der Browser ihn liefert) wird in die Tonspur gemischt. Vor dem ersten Teilen ein
// Datenschutzhinweis, waehrend des Teilens eine rote Leiste "Du teilst deinen Bildschirm".
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
  // 2.4: Faehigkeiten dieses Clients im signierten Body (offer/answer). reneg = offer/answer waehrend des Gespraechs
  // (auch ICE-Restart), want = beachtet state.want des Gegenuebers, bye = meldet das Verlassen
  // reneg nur, wenn auch der eigene Server sie meldet (GET /api/calls/config -> features)
  // screen2 (E3) = zweite Videospur fuer den Bildschirm; braucht reneg und die Server-Faehigkeit screen2
  var CAPS_ALL = ['reneg', 'want', 'bye', 'screen2'];
  function feat(x) { return !!(cfg && (cfg.features || []).indexOf(x) >= 0); }
  function myCaps() { return CAPS_ALL.filter(function (x) { return x === 'reneg' ? feat('reneg') : x === 'screen2' ? feat('reneg') && feat('screen2') : true; }); }
  var RESTART_MAX = 3, DISC_S = 2.5, FAIL_S = 20;    // ICE-Restart: Versuche je Abbruch, Wartezeit bei disconnected, Frist bei failed
  // Bitrate-Regeln je Weg (Konzept 2.4 §1.3), kbit/s. cam/screen/mesh je Strom, H = hoechste Bildhoehe, fps = Bildrate
  var RATES = {
    direct: { cam: 1500, camH: 720, screen: 2500, screenFps: 15, mesh: 600, meshH: 540, audio: 32 },
    'relay-udp': { cam: 1200, camH: 720, screen: 1800, screenFps: 15, mesh: 500, meshH: 540, audio: 32 },
    'relay-tcp': { cam: 800, camH: 540, screen: 1200, screenFps: 8, mesh: 350, meshH: 360, meshFps: 15, audio: 24 }
  };
  var PATHS = ['direct', 'relay-udp', 'relay-tcp'];
  // -> Sende-Parameter fuer eine Spur: kind 'cam'|'screen'|'audio', path, want ('large'|'small'|'off') des Gegenuebers,
  // n Endpunkte (ab 3: Mesh-Werte, E5), h Bildhoehe der Quelle, share = ich teile gerade daneben den Bildschirm (Betriebsart
  // B: Kamera <= 300 kbit/s, ueber Relais TCP aus - Summe je Richtung <= 1,6 Mbit/s, Konzept §4.5)
  var SHARE_CAM = 300;
  function rules(kind, path, want, n, h, share) {
    var r = RATES[path] || RATES.direct, o = {};
    if (kind === 'audio') return { maxBitrate: r.audio * 1000 };
    if (kind === 'screen') {                          // Bildschirm: Text muss lesbar bleiben - want aendert nichts
      o = { maxBitrate: r.screen * 1000, maxFramerate: r.screenFps, degradationPreference: 'maintain-resolution', scaleResolutionDownBy: 1 };
      return o;
    }
    var mesh = (n || 2) >= 3, br = mesh ? r.mesh : r.cam, capH = mesh ? r.meshH : r.camH;
    o = { maxBitrate: br * 1000, scaleResolutionDownBy: h && h > capH ? Math.round(h / capH * 100) / 100 : 1, active: true };
    if (mesh && r.meshFps) o.maxFramerate = r.meshFps;
    if (want === 'small') {                           // Gegenueber zeigt uns klein (Bild-im-Bild, Tab im Hintergrund)
      o.maxBitrate = Math.min(o.maxBitrate, mesh ? 200000 : 300000);
      o.scaleResolutionDownBy = Math.max(2, Math.min(3, (h || 480) / 240));
    } else if (want === 'off') o.active = false;
    if (share && kind === 'cam') {
      o.maxBitrate = Math.min(o.maxBitrate, SHARE_CAM * 1000);
      o.scaleResolutionDownBy = Math.max(o.scaleResolutionDownBy, Math.max(1, (h || 480) / 360));
      o.scaleResolutionDownBy = Math.round(o.scaleResolutionDownBy * 100) / 100;
      if (path === 'relay-tcp') o.active = false;
    }
    return o;
  }
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
    x: ['M6 6l12 12', 'M18 6L6 18'],
    pip: ['M3 5h18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z', 'M13 12h6v5h-6z'],
    back: ['M3 5h18a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z', 'M14 10l-4 4', 'M10 10v4h4'],
    swap: ['M7 4L3 8l4 4', 'M3 8h14', 'M17 20l4-4-4-4', 'M21 16H7']
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

  // getMedia mit Hinweis, wenn die Freigabe-Abfrage des Browsers offen bleibt (Safari fragt oft erst nach dem Klick)
  function mediaHint(c, video) {
    var t = setTimeout(function () {
      if (C === c && !c.local) status(c, video ? T('Bitte Kamera und Mikrofon im Browser erlauben – die Abfrage steht oben bzw. neben der Adresse.')
        : T('Bitte das Mikrofon im Browser erlauben – die Abfrage steht oben bzw. neben der Adresse.'));
    }, 2500);
    return getMedia(video).then(function (s) { clearTimeout(t); return s; }, function (e) { clearTimeout(t); throw e; });
  }

  // --- Anruf-Objekt ----------------------------------------------------------------------------------------------
  function newCall(o) {
    return Object.assign({ id: null, chat_id: null, role: 'caller', media: 'audio', peer: {}, peerDevs: {}, peerDevice: null,
      pc: null, local: null, cam: null, screen: null, remote: new MediaStream(), mic: true, seen: {}, early: [], remoteIce: [],
      outIce: [], outT: null, ready: false, connected: false, verified: false, path: '', fps: [], t0: 0, alive: null, statsT: null,
      ending: false, remoteState: { mic: true, cam: false, screen: false, pip: false, want: 'large', path: '' }, ui: null,
      // 2.4 (saga-call v3)
      peerCaps: [], established: false, n: 0, remoteN: 0, makingOffer: false, ignoreOffer: false, restarts: 0, restarting: false,
      lastUf: null, oldUf: {}, net: '', pip: false, pipWin: null, wantSent: 'large', rates: {}, sent: {},
      // 2.4 E3: eigene Transceiver (Ton, Kamera, Bildschirm), Betriebsart des Teilens ('track' = B, 'replace' = A),
      // Rollen der m-Zeilen beim Gegenueber (mid -> mic|cam|screen), Fernspuren je mid, Ansicht getauscht
      micTr: null, camTr: null, scrTr: null, scrStream: null, scrMode: '', mix: null, peerTracks: {}, rmid: {}, swap: false,
      shareStarts: 0 }, o);
  }
  function myDev() { return bridge().device(); }
  function X() { return bridge().crypto(); }
  // 2.3: Gegenueber auf einem anderen Server (c.v2): saga-call/v2 mit foederierten Handles statt Benutzernamen
  function me(c) { return c.v2 ? bridge().myHandle() : bridge().me(); }
  function them(c) { return c.v2 ? c.peer.handle : c.peer.username; }
  function peerKeys(c, fresh) {
    if (!fresh && c._keys) return c._keys;
    c._keys = (c.v2 ? bridge().peerKeysV2(c.peer.handle, c.peer.name) : bridge().peerKeys(c.peer.username, c.peer.name)).then(function (k) {
      c.peerDevs = {}; k.devices.forEach(function (d) { c.peerDevs[d.id] = d; }); return k;
    }, function (e) { c._keys = null; throw e; });
    return c._keys;
  }
  function seal(c, toDev, body) {
    var d = myDev(), pd = c.peerDevs[toDev];
    if (c.v2) return X().sealSignalV2({ call_id: c.id, from_handle: me(c), from_device: d.id, sign_sk: d.sign_sk, to_handle: them(c),
                                        to_device: toDev, to_pk: pd.enc_pk, body: body });
    return X().sealSignal({ call_id: c.id, from_uid: bridge().me(), from_device: d.id, sign_sk: d.sign_sk, to_uid: c.peer.username,
                            to_device: toDev, to_pk: pd.enc_pk, body: body });
  }
  // body: Text oder Funktion(geraet) -> Text (2.4: je Zielgeraet eigenes pc)
  function sealAll(c, devs, body) { var out = {}; devs.forEach(function (id) { out[id] = seal(c, id, typeof body === 'function' ? body(id) : body); }); return out; }
  // 2.4: Kennung der PeerConnection = sortiertes Paar der beiden Geraete-IDs; hoeflich ist die groessere device_id
  function pair(a, b) { return a < b ? a + ':' + b : b + ':' + a; }
  // Der Anrufer legt die Kennung fest (je Zielgeraet das sortierte Paar), der Angerufene uebernimmt sie aus dem Angebot
  function pcId(c) { return c.pcFixed || (c.peerDevice ? pair(myDev().id, c.peerDevice) : ''); }
  function polite(c) { return myDev().id > c.peerDevice; }
  function peerCan(c, cap) { return (c.peerCaps || []).indexOf(cap) >= 0; }
  function canReneg(c) { return peerCan(c, 'reneg') && myCaps().indexOf('reneg') >= 0; }    // beide Seiten muessen reneg melden
  // Betriebsart B (zweite Videospur) nur, wenn beide Seiten reneg UND screen2 melden
  function canScreen2(c) { return canReneg(c) && peerCan(c, 'screen2') && myCaps().indexOf('screen2') >= 0; }
  function capsOf(b) { return Array.isArray(b && b.caps) ? b.caps.filter(function (x) { return typeof x === 'string'; }).slice(0, 16) : []; }
  function targets(c) { return c.peerDevice ? [c.peerDevice] : Object.keys(c.peerDevs); }
  // Signal oeffnen + pruefen -> Objekt oder null (wirft nicht). Unbekanntes Geraet: Schluessel einmal neu holen.
  function openSig(c, s) {
    var d = myDev();
    var go = function () {
      var pd = c.peerDevs[s.from_device]; if (!pd) return null;
      var o = c.v2 ? X().openSignalV2(s.sealed, d.enc_pk, d.enc_sk) : X().openSignal(s.sealed, d.enc_pk, d.enc_sk);
      var ok = c.v2 ? X().verifySignalV2({ call_id: c.id, from_handle: them(c), from_device: s.from_device, to_handle: me(c), to_device: d.id, body: o.body }, o.sig, pd.sign_pk)
        : X().verifySignal({ call_id: c.id, from_uid: c.peer.username, from_device: s.from_device, to_uid: bridge().me(), to_device: d.id, body: o.body }, o.sig, pd.sign_pk);
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
    c.sent[body.t] = (c.sent[body.t] || 0) + 1;
    var me_ = myDev().id;
    return api('POST', '/api/calls/' + c.id + '/signal', { device: me_, signals: sealAll(c, devs, function (id) {
      return JSON.stringify(Object.assign({}, body, { pc: c.pcFixed || pair(me_, id) }));
    }) }).catch(function (e) {
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
      if (e.transceiver && e.transceiver.mid != null) c.rmid[t.id] = String(e.transceiver.mid);    // E3: Rolle ueber die mid
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
    pc.oniceconnectionstatechange = function () {
      onConn(c);              // auch neuere Browser: nach einem ICE-Restart bleibt connectionState oft "connected"
    };
    // 2.4: Neuverhandlung (Perfect Negotiation) erst nach dem ersten Angebot/Antwort und nur mit einer 2.4-Gegenstelle
    pc.onnegotiationneeded = function () { if (C === c && c.established && canReneg(c) && !c.done) offerNow(c); };
    return pc;
  }
  function addLocal(c, pc, fromOffer) {
    var a = c.local.getAudioTracks()[0] || null, v = c.local.getVideoTracks()[0] || null;
    c.cam = v;
    drawCtl(c);                                        // Kamera-Knopf: beim Klingeln ohne Kamera gezeichnet (Bughunt 2)
    if (a) a.enabled = c.mic;                          // Mikrofon wirklich senden (Stumm-Zustand gilt von Anfang an)
    if (!fromOffer) {
      c.micTr = pc.addTransceiver(a || 'audio', { direction: 'sendrecv', streams: [c.local] });
      c.camTr = pc.addTransceiver(v || 'video', { direction: 'sendrecv', streams: [c.local] });    // immer: Kamera/Bildschirm spaeter ohne neues Angebot
      return;
    }
    // Antwort: Spuren erst fertig einsetzen, dann createAnswer (nicht jeder Browser reiht replaceTrack in die
    // Operationskette ein - sonst kann die Antwort ohne Ton-Spur entstehen)
    var wait = [];
    pc.getTransceivers().forEach(function (tr) {
      var kind = tr.receiver && tr.receiver.track && tr.receiver.track.kind;
      // Erstes Angebot: eine Ton- und eine Video-m-Zeile (weitere, z. B. ein Bildschirm einer spaeteren Fassung, nur empfangen)
      if (kind === 'audio' && c.micTr || kind === 'video' && c.camTr) { tr.direction = 'recvonly'; return; }
      if (kind === 'audio') c.micTr = tr; else if (kind === 'video') c.camTr = tr;
      tr.direction = 'sendrecv';
      if (kind === 'audio' && a) wait.push(tr.sender.replaceTrack(a));
      if (kind === 'video') { if (v) wait.push(tr.sender.replaceTrack(v)); tr.sender.setStreams && tr.sender.setStreams(c.local); }
      if (kind === 'audio' && tr.sender.setStreams) tr.sender.setStreams(c.local);
    });
    return Promise.all(wait);
  }
  function videoSender(c) {
    if (c.camTr) return c.camTr.sender;
    var s = null; c.pc.getTransceivers().forEach(function (tr) { if (!s && tr.receiver.track && tr.receiver.track.kind === 'video') s = tr.sender; });
    return s;
  }
  function audioSender(c) {
    if (c.micTr) return c.micTr.sender;
    var s = null; c.pc.getTransceivers().forEach(function (tr) { if (!s && tr.receiver.track && tr.receiver.track.kind === 'audio') s = tr.sender; });
    return s;
  }
  // --- 2.4 E3: Rollen der m-Zeilen (Vertrag docs/APP-API.md "Bildschirm v2") -------------------------------------------
  // tracks = {mid: "mic"|"cam"|"screen"} fuer die eigenen Transceiver (feste Rolle je m-Zeile), msid = {mid: Stream-ID}
  function myTracks(c) {
    var o = {}, m = {};
    [[c.micTr, 'mic', c.local], [c.camTr, 'cam', c.local], [c.scrTr, 'screen', c.scrStream]].forEach(function (x) {
      if (!x[0] || x[0].mid == null) return;
      o[x[0].mid] = x[1];
      if (x[2]) m[x[0].mid] = x[2].id;
    });
    return { tracks: o, msid: m };
  }
  // mid, auf der gerade der eigene Bildschirm laeuft (B: eigene m-Zeile, A: die Kamera-m-Zeile) - sonst null
  function myScreenMid(c) {
    if (!c.screen) return null;
    var tr = c.scrMode === 'track' ? c.scrTr : c.camTr;
    return tr && tr.mid != null ? String(tr.mid) : null;
  }
  var ROLES = ['mic', 'cam', 'screen'];
  function tracksOf(x) {
    var o = {}, n = 0;
    if (!x || typeof x !== 'object' || Array.isArray(x)) return o;
    Object.keys(x).forEach(function (k) { if (n < 8 && /^[A-Za-z0-9_-]{1,16}$/.test(k) && ROLES.indexOf(x[k]) >= 0) { o[k] = x[k]; n++; } });
    return o;
  }
  // Fernspuren: welche Videospur ist Kamera, welche Bildschirm? Massgeblich ist state.screen_mid (bzw. die Rolle "screen"
  // der m-Zeile); ohne Angaben (2.3/E0, Betriebsart A) ist die einzige Videospur bei screen=true der Bildschirm.
  function remoteVideo(c) {
    var vids = c.remote.getVideoTracks().filter(function (t) { return t.readyState === 'live'; });
    var byMid = {}; vids.forEach(function (t) { var m = c.rmid[t.id]; if (m != null) byMid[m] = t; });
    var camMid = null;
    Object.keys(c.peerTracks).forEach(function (m) { if (c.peerTracks[m] === 'cam' && camMid == null) camMid = m; });
    if (camMid == null && c.pc) {                                  // ohne Rollen: erste Video-m-Zeile = Kamera
      c.pc.getTransceivers().some(function (tr) { if (tr.receiver.track && tr.receiver.track.kind === 'video' && tr.mid != null) { camMid = String(tr.mid); return true; } return false; });
    }
    var cam = camMid != null && byMid[camMid] ? byMid[camMid] : (vids.length && !Object.keys(byMid).length ? vids[0] : null);
    var rs = c.remoteState, scr = null;
    if (rs.screen) {
      if (rs.screenMid != null && byMid[rs.screenMid]) scr = byMid[rs.screenMid];
      else if (rs.screenMid == null) { scr = cam; cam = null; }        // Betriebsart A: Bildschirm statt Kamera
      if (scr === cam) cam = null;
    }
    var on = function (t) { return t && !t.muted ? t : null; };
    return { cam: rs.screen && rs.screenMid == null ? null : on(cam), screen: on(scr), camAny: cam, screenAny: scr };
  }
  function remoteUfrag(c) {
    var d = c.pc && c.pc.remoteDescription, m = d && /a=ice-ufrag:(\S+)/.exec(d.sdp || '');
    return m ? m[1] : null;
  }
  // Kandidaten erst anwenden, wenn die passende Beschreibung (ice-ufrag) gesetzt ist: nach einem ICE-Restart kommen neue
  // Kandidaten oft vor der neuen Antwort an; Kandidaten alter Generationen fallen weg
  function applyRemoteIce(c) {
    if (!c.pc || !c.pc.remoteDescription) return;
    var uf = remoteUfrag(c), keep = [];
    if (c.lastUf && uf && c.lastUf !== uf) c.oldUf[c.lastUf] = true;
    c.lastUf = uf;
    c.remoteIce.splice(0).forEach(function (x) {
      if (x.usernameFragment && uf && x.usernameFragment !== uf) { if (!c.oldUf[x.usernameFragment]) keep.push(x); return; }
      c.pc.addIceCandidate(x).catch(function () { /* veraltet/ungueltig */ });
    });
    c.remoteIce = keep.slice(-120);
  }
  // Unbekannte Typen ("t") werden ignoriert - so koennen spaetere Fassungen neue Nachrichten schicken (Vertrag v3)
  function handle(c, b) {
    if (!b) { securityFail(c); return; }
    if (b.pc && c.peerDevice && b.pc !== pcId(c)) return;            // andere PeerConnection (Mehrgeraete/Mesh ab E4)
    if (b.t === 'ice') { (Array.isArray(b.c) ? b.c : []).slice(0, 60).forEach(function (x) { if (x && x.candidate) c.remoteIce.push(x); }); applyRemoteIce(c); }
    else if (b.t === 'answer' && c.role === 'caller' && !c.pc.remoteDescription) {
      if (c.peerDevice && b._from !== c.peerDevice) return;
      if (!fpOk(b)) { securityFail(c); return; }
      c.peerDevice = b._from;                          // ab jetzt Signale nur noch an das annehmende Geraet
      c.fps = X().sdpFingerprints(b.sdp);
      c.peerCaps = capsOf(b); peerTracksIn(c, b);
      c.pc.setRemoteDescription({ type: 'answer', sdp: b.sdp }).then(function () { c.established = true; applyRemoteIce(c); }).catch(function (e) { fail(c, T('Verbindung nicht aufgebaut: {err}', { err: e.message })); });
    } else if (b.t === 'offer' && c.established && b._from === c.peerDevice) reOffer(c, b);
    else if (b.t === 'answer' && c.established && b._from === c.peerDevice) reAnswer(c, b);
    else if (b.t === 'state') {
      var w = b.want && typeof b.want === 'object' && ['off', 'small', 'large'].indexOf(b.want.video) >= 0 ? b.want.video : 'large';
      var sm = typeof b.screen_mid === 'string' && /^[A-Za-z0-9_-]{1,16}$/.test(b.screen_mid) ? b.screen_mid : null;
      var was = c.remoteState.screen;
      c.remoteState = { mic: b.mic !== false, cam: !!b.cam, screen: !!b.screen, pip: !!b.pip, want: w,
                        path: PATHS.indexOf(b.path) >= 0 ? b.path : '', screenMid: b.screen ? sm : null, audio: !!b.screen_audio };
      peerTracksIn(c, b);
      if (c.remoteState.screen && !was) c.swap = false;               // neues Teilen: Bildschirm gross
      c.gotState = true; bindRemote(c); drawMedia(c); drawInfo(c); applyRates(c);
    } else if (b.t === 'bye' && c.established && b._from === c.peerDevice) remoteEnded(c, { reason: 'hangup' });
  }
  function peerTracksIn(c, b) {
    var t = tracksOf(b && b.tracks);
    if (Object.keys(t).length) c.peerTracks = t;
  }
  // --- 2.4: Neuverhandlung ("Perfect Negotiation") und ICE-Restart ------------------------------------------------------
  function sdpBody(c, t, sdp, n) {
    var o = { t: t, sdp: sdp, fp: X().sdpFingerprints(sdp), n: n, caps: myCaps(), media: c.media };
    if (myCaps().indexOf('screen2') >= 0) { var m = myTracks(c); o.tracks = m.tracks; o.msid = m.msid; }    // E3: Rolle je m-Zeile
    return o;
  }
  // Eigenes Angebot waehrend des Gespraechs (negotiationneeded, ICE-Restart). setLocalDescription() ohne Argument erzeugt
  // das passende Angebot (inkl. iceRestart nach restartIce())
  function offerNow(c, restart) {
    var pc = c.pc;
    c.makingOffer = true;
    var p = restart && !pc.restartIce ? pc.createOffer({ iceRestart: true }).then(function (o) { return pc.setLocalDescription(o); }) : pc.setLocalDescription();
    return p.then(function () {
      if (C !== c || pc.signalingState !== 'have-local-offer') return;
      c.n += 1;
      return sendSignal(c, Object.assign(sdpBody(c, 'offer', pc.localDescription.sdp, c.n), c.restarting ? { restart: true } : {})).then(function () {
        if (c.stateDue) { c.stateDue = false; stateOut(c); }            // E3: Zustand (screen_mid) erst, wenn die mid feststeht
      });
    }).catch(function () { /* naechster Versuch beim naechsten Anlass */ }).then(function () { c.makingOffer = false; });
  }
  function sameFp(c, b) { return fpOk(b) && JSON.stringify(X().sdpFingerprints(b.sdp)) === JSON.stringify(c.fps.slice().sort()); }
  function reOffer(c, b) {
    var pc = c.pc;
    if (typeof b.n !== 'number' || b.n <= c.remoteN) return;           // alt oder doppelt
    var collision = c.makingOffer || pc.signalingState !== 'stable';
    c.ignoreOffer = !polite(c) && collision;
    if (c.ignoreOffer) return;                                         // unhoeflich: eigenes Angebot gewinnt
    // Der DTLS-Schluessel bleibt fuer die ganze Verbindung gleich: ein anderer Fingerabdruck waere ein Angriff
    if (!sameFp(c, b)) { securityFail(c); return; }
    c.remoteN = b.n;
    if (capsOf(b).length) c.peerCaps = capsOf(b);
    peerTracksIn(c, b);
    pc.setRemoteDescription({ type: 'offer', sdp: b.sdp }).then(function () {          // bei Kollision: implizites Rollback
      applyRemoteIce(c);
      return pc.setLocalDescription();
    }).then(function () {
      if (C !== c) return;
      return sendSignal(c, sdpBody(c, 'answer', pc.localDescription.sdp, b.n)).then(function () { settled(c); });
    }).catch(function () { /* Gegenueber versucht es erneut (ICE-Restart) */ });
  }
  function reAnswer(c, b) {
    var pc = c.pc;
    if (pc.signalingState !== 'have-local-offer' || b.n !== c.n) return;   // Antwort auf ein ueberholtes Angebot
    if (!sameFp(c, b)) { securityFail(c); return; }
    peerTracksIn(c, b);
    pc.setRemoteDescription({ type: 'answer', sdp: b.sdp }).then(function () { applyRemoteIce(c); settled(c); }).catch(function () {});
  }
  // Neuverhandlung abgeschlossen: steht ICE schon (Restart ohne Unterbrechung), gilt der Neuaufbau als fertig
  function settled(c) {
    var x = c.pc && c.pc.iceConnectionState;
    if (C === c && c.pc.signalingState === 'stable' && (x === 'connected' || x === 'completed')) onConn(c);
  }
  // ICE neu aushandeln (Netzwechsel WLAN <-> LTE, Verbindung weg). Nur mit einer 2.4-Gegenstelle; sonst wie bisher.
  // Hoechstens RESTART_MAX Versuche je Abbruch (zurueckgesetzt, sobald die Verbindung wieder steht); kommt sie nach
  // FAIL_S Sekunden nicht wieder, endet der Anruf wie bisher
  function restartIce(c) {
    if (!c || C !== c || !c.pc || c.done || !c.established || !canReneg(c)) return false;
    if (c.restarting && Date.now() - c.restartAt < 6000) return true;
    if (c.tries >= RESTART_MAX) return false;
    c.restarting = true; c.restartAt = Date.now(); c.restarts += 1; c.tries = (c.tries || 0) + 1;
    status(c, T('Verbindung wird neu aufgebaut …'));
    if (!c.failT) c.failT = setTimeout(function () { c.failT = null; if (C === c && c.restarting) iceFailed(c); }, FAIL_S * 1000);
    if (c.pc.restartIce) c.pc.restartIce();                       // -> negotiationneeded -> offerNow
    else offerNow(c, true);
    return true;
  }
  var netT = null;
  function netChanged() {
    clearTimeout(netT);
    netT = setTimeout(function () { var c = C; if (c && c.connected && !c.done) restartIce(c); }, 800);
  }
  window.addEventListener('online', netChanged);
  if (navigator.connection && navigator.connection.addEventListener) navigator.connection.addEventListener('change', netChanged);
  function fpOk(b) {
    var fp = X().sdpFingerprints(b.sdp);
    return fp.length > 0 && JSON.stringify(fp) === JSON.stringify((b.fp || []).slice().sort());
  }
  function signalIn(d) {   // (2.4: Reihenfolge je Geraet bleibt - die Signale kommen in seq-Folge an)
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
    if (chat.v === 2 && (chat.request || chat.readonly)) { HK.toast(T('Anrufe gehen erst, wenn beide die Unterhaltung angenommen haben.')); return; }
    if (!window.RTCPeerConnection) { HK.toast(T('Dieser Browser kann keine Anrufe (WebRTC fehlt).')); return; }
    var c = C = newCall({ chat_id: chat.id, role: 'caller', media: video ? 'video' : 'audio', peer: chat.peer, v2: chat.v === 2 });
    audioCtx();
    win(c); status(c, T('Kamera und Mikrofon …'));
    config(true).then(function () {
      if (!cfg.enabled) throw new Error(T('Anrufe sind auf diesem Server abgeschaltet.'));
      return peerKeys(c);
    }).then(function (k) {
      if (!k.devices.length) throw new Error(T('{name} hat Chats noch auf keinem Gerät eingerichtet – Anrufe gehen erst danach.', { name: c.peer.name }));
      return mediaHint(c, video);
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
    var sdp = c.pc.localDescription.sdp, me_ = myDev().id;
    c.n = 1;
    var body = function (id) { return JSON.stringify(Object.assign(sdpBody(c, 'offer', sdp, 1), { pc: pair(me_, id) })); };
    return api('POST', '/api/calls', { id: c.id, chat_id: c.chat_id, media: c.media, device: me_, signals: sealAll(c, Object.keys(c.peerDevs), body), caps: myCaps() })
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
      c.call = r.call; c.chat_id = r.call.chat_id; c.media = r.call.media; c.peer = r.call.peer; c.v2 = r.call.v === 2; drawPeer(c);
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
      c.peerCaps = capsOf(offer); c.remoteN = typeof offer.n === 'number' ? offer.n : 1; peerTracksIn(c, offer);
      if (typeof offer.pc === 'string' && /^[0-9a-f:]{1,200}$/.test(offer.pc)) c.pcFixed = offer.pc;
      return mediaHint(c, video);
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
      var sdp = c.pc.localDescription.sdp, body = JSON.stringify(Object.assign(sdpBody(c, 'answer', sdp, c.remoteN), { pc: pcId(c) }));
      var sig = {}; sig[c.peerDevice] = seal(c, c.peerDevice, body);
      return api('POST', '/api/calls/' + id + '/answer', { device: dev, signals: sig, caps: myCaps() });
    }).then(function () {
      if (C !== c) return;
      c.ready = true; c.t0 = 0; c.established = true;
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
    var st = c.pc.connectionState || c.pc.iceConnectionState, ice = c.pc.iceConnectionState;
    if ((st === 'connected' || st === 'completed') && ice !== 'failed' && ice !== 'disconnected') {
      clearTimeout(c.failT); clearTimeout(c.discT); c.discT = null; c.failT = null; c.tries = 0;
      if (c.restarting) { c.restarting = false; c.path = ''; c.net = ''; }
      if (!c.connected) { c.connected = true; c.t0 = Date.now(); stopTone(); clearTimeout(c.connT); stateOut(c); }
      status(c, ''); tick(c); poll(c);
    } else if (st === 'disconnected' || ice === 'disconnected') {
      status(c, T('Verbindung unterbrochen – versuche es weiter …'));
      // 2.4: kommt die Verbindung nicht von selbst wieder (Netzwechsel), ICE neu aushandeln
      if (!c.discT) c.discT = setTimeout(function () { c.discT = null; var x = c.pc && c.pc.iceConnectionState; if (C === c && (x === 'disconnected' || x === 'failed')) restartIce(c); }, DISC_S * 1000);
    } else if (st === 'failed' || ice === 'failed') {
      if (!restartIce(c) && !c.failT) iceFailed(c);     // laufende Frist (failT) abwarten, sonst wie bisher beenden
    }
  }
  function iceFailed(c) {
    fail(c, cfg && cfg.turn ? T('Keine Verbindung möglich – auch nicht über den TURN-Server. Bitte später erneut versuchen.')
      : T('Direktverbindung nicht möglich (z. B. strenge Firewall oder Mobilfunknetz). Der Admin kann einen TURN-Server eintragen.'), 'failed');
  }
  // Nach dem Verbinden: DTLS-Fingerabdruck des Gegenuebers (getStats) muss zur SIGNIERTEN SDP passen; Weg (direkt/Relais)
  // Alle 2 s ein getStats: Fingerabdruck pruefen, Weg erkennen, Qualitaet, Bitrate-Regeln anwenden
  function poll(c) {
    if (!c.pc) return;
    c.pc.getStats().then(function (rep) {
      if (C !== c) return;
      checkPath(c, rep); quality(c, rep); applyRates(c);
    }).catch(function () {});
  }
  function checkPath(c, rep) {
    (function () {
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
        // 2.4: genauer Weg fuer die Bitrate-Regeln. Ob das GEGENUEBER ueber TCP relayt, sieht man hier nicht - das meldet
        // es selbst in state.path
        // ICE-TCP ohne Relais hat dieselben Nachteile (Head-of-Line-Blocking) -> Grenzen von "Relais TCP"
        var net = l && l.candidateType === 'relay' ? (/^(tcp|tls)$/.test(String(l.relayProtocol || l.protocol || '').toLowerCase()) ? 'relay-tcp' : 'relay-udp')
          : l && String(l.protocol || '').toLowerCase() === 'tcp' ? 'relay-tcp' : c.path === 'relay' ? 'relay-udp' : 'direct';
        if (net !== c.net) { c.net = net; if (c.connected) stateOut(c); }
      }
      drawInfo(c);
    })();
  }
  // Schlechterer Weg von beiden Seiten
  function pathOf(c) {
    var a = PATHS.indexOf(c.net), b = PATHS.indexOf(c.remoteState.path);
    return PATHS[Math.max(a, b, 0)];
  }
  // Sende-Parameter nach §1.3 setzen (nur bei Aenderung; setParameters ist billig, aber nicht kostenlos)
  function applyRates(c) {
    if (!c.pc || !c.pc.getSenders || C !== c) return;
    var path = pathOf(c), want = c.remoteState.want || 'large', share = !!(c.screen && c.scrMode === 'track');
    c.pc.getSenders().forEach(function (snd) {
      var tr = snd.track; if (!tr || !snd.getParameters || !snd.setParameters) return;
      var kind = tr.kind === 'audio' ? 'audio' : tr === c.screen ? 'screen' : 'cam';
      // Platz je Sender: audio, video (Kamera-m-Zeile, in Betriebsart A auch der Bildschirm), screen (eigene m-Zeile, E3)
      var slot = tr.kind === 'audio' ? 'audio' : c.scrTr && snd === c.scrTr.sender ? 'screen' : 'video';
      var h = 0; try { h = (tr.getSettings && tr.getSettings().height) || 0; } catch (e) { h = 0; }
      var want_ = rules(kind, path, want, 2, h, share), key = kind + ':' + JSON.stringify(want_);
      if (c.rates[slot] === key) return;
      var p = snd.getParameters();
      if (!p.encodings || !p.encodings.length) return;                 // noch nicht ausgehandelt
      var e = p.encodings[0];
      ['maxBitrate', 'maxFramerate', 'scaleResolutionDownBy', 'active'].forEach(function (k) { if (want_[k] !== undefined) e[k] = want_[k]; });
      if (kind !== 'screen' && e.maxFramerate && want_.maxFramerate === undefined) delete e.maxFramerate;
      if (tr.kind === 'video') p.degradationPreference = want_.degradationPreference || 'balanced';
      c.rates[slot] = key;
      var done = function () { c.applied = c.applied || {}; c.applied[slot] = Object.assign({ kind: kind, path: path, want: want }, want_); };
      snd.setParameters(p).then(done, function () {
        // aeltere Browser kennen degradationPreference nicht: ohne nochmal (frische Parameter, neue Transaktion)
        var q = snd.getParameters(); if (!q.encodings || !q.encodings.length) throw new Error('-');
        ['maxBitrate', 'maxFramerate', 'scaleResolutionDownBy', 'active'].forEach(function (k) { if (want_[k] !== undefined) q.encodings[0][k] = want_[k]; });
        return snd.setParameters(q).then(done);
      }).catch(function () { if (c.rates[slot] === key) delete c.rates[slot]; });
    });
    // Relais TCP: Kamera waehrend des Teilens aus (Hinweis beim Teilenden)
    var off = share && path === 'relay-tcp' && !!c.cam;
    if (off !== !!c.camPaused) { c.camPaused = off; drawShare(c); }
  }
  function quality(c, rep) {
    (function () {
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
    })();
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
  // E3: zwei Videospuren (Kamera und Bildschirm) - gross der Bildschirm, klein die Kamera; c.swap tauscht
  function bindRemote(c) {
    if (!c.ui) return;
    var live = function (t) { return t.readyState === 'live'; };
    var rvt = remoteVideo(c), big = [], small = [];
    if (rvt.screen) {
      var sc = rvt.screen, cm = rvt.camAny && c.remoteState.cam ? rvt.camAny : null;
      if (c.swap && cm) { big = [cm]; small = [sc]; } else { big = [sc]; small = cm ? [cm] : []; }
    } else if (rvt.camAny) big = [rvt.camAny];
    else big = c.remote.getVideoTracks().filter(live).slice(0, 1);
    [[c.ui.ra, c.remote.getAudioTracks().filter(live)], [c.ui.rv, big.filter(live)], [c.ui.rv2, small.filter(live)]].forEach(function (x) {
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
    c.statsT = setInterval(function () { if (C !== c) return clearInterval(c.statsT); poll(c); }, 2000);
  }
  function aliveOn(c) {
    clearInterval(c.alive);
    c.alive = setInterval(function () {
      if (C !== c || !c.id) return clearInterval(c.alive);
      api('POST', '/api/calls/' + c.id + '/alive', {}).then(function (r) { if (r.state === 'ended') remoteEnded(c, { reason: r.reason }); }).catch(function () {});
    }, ((cfg && cfg.alive_s) || 15) * 1000);
  }

  // --- Bedienung -------------------------------------------------------------------------------------------------
  // Zustand ans Gegenueber. 2.4: dazu pip, want (wie gross es uns zeigen soll) und path - immer, auch ohne reneg
  // (aeltere Gegenstellen ignorieren die Felder)
  function wantNow(c) { return c.pip || document.visibilityState === 'hidden' ? 'small' : 'large'; }
  function stateOut(c) {
    if (!c.ready) return;
    c.wantSent = wantNow(c);
    var o = { t: 'state', mic: c.mic, cam: !!(c.cam && c.cam.readyState === 'live' && c.cam.enabled), screen: !!c.screen,
              pip: !!c.pip, want: { video: c.wantSent }, path: c.net || undefined };
    // E3: welche m-Zeile was traegt (nur fuer 2.4-Gegenstellen mit screen2; aeltere ignorieren unbekannte Felder ohnehin)
    if (myCaps().indexOf('screen2') >= 0) {
      var m = myTracks(c), sm = myScreenMid(c);
      o.tracks = m.tracks; o.msid = m.msid;
      if (sm != null) o.screen_mid = sm;
      if (c.screen) o.screen_audio = !!c.mix;
    }
    sendSignal(c, o);
  }
  var visT = null;
  document.addEventListener('visibilitychange', function () {
    clearTimeout(visT);
    visT = setTimeout(function () { var c = C; if (c && c.connected && !c.done && wantNow(c) !== c.wantSent) stateOut(c); }, 600);
  });
  function toggleMic(c) {
    c.mic = !c.mic;
    (c.local ? c.local.getAudioTracks() : []).forEach(function (t) { t.enabled = c.mic; });
    try { if (navigator.mediaSession && navigator.mediaSession.setMicrophoneActive) navigator.mediaSession.setMicrophoneActive(c.mic); } catch (e) { /* egal */ }
    drawCtl(c); stateOut(c);
  }
  function toggleCam(c) {
    var snd = c.pc && videoSender(c);
    if (c.cam) {
      c.cam.stop(); if (c.local) c.local.removeTrack(c.cam); c.cam = null;
      if (snd && (!c.screen || c.scrMode === 'track')) snd.replaceTrack(null);
      drawMedia(c); drawCtl(c); stateOut(c); return;
    }
    navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' } }).then(function (s) {
      if (C !== c) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
      c.cam = s.getVideoTracks()[0]; c.local.addTrack(c.cam);
      if (snd && (!c.screen || c.scrMode === 'track')) snd.replaceTrack(c.cam).then(function () { delete c.rates.video; applyRates(c); });
      drawMedia(c); drawCtl(c); stateOut(c);
    }).catch(function (e) { HK.toast(mediaError(e, true)); });
  }
  // --- 2.4 E3: Bildschirm teilen ------------------------------------------------------------------------------------
  // Betriebsart B ('track'): eigene Videospur ueber Neuverhandlung, die Kamera bleibt (gedrosselt). A ('replace', 2.1/2.3
  // und E0-Gegenstellen ohne screen2): replaceTrack auf dem Kamera-Sender wie bisher.
  function hintOff() { try { return localStorage.getItem('saga-screen-hint') === '0'; } catch (e) { return false; } }
  function toggleScreen(c) {
    if (!c.pc || !videoSender(c)) return;
    if (c.screen) { stopScreen(c); return; }
    if (hintOff()) { pickScreen(c); return; }
    screenHint(c);
  }
  // Einmaliger Hinweis vor dem ersten Teilen (je Geraet abschaltbar). Der Knopf "Bildschirm waehlen" ist die Bedienung,
  // die der Browser fuer getDisplayMedia verlangt.
  function screenHint(c) {
    if (!c.ui || c.ui.hint) return;
    var box = el('div', 'cl-hint'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', T('Bildschirm teilen'));
    box.appendChild(el('b', null, T('Bildschirm teilen')));
    box.appendChild(el('p', null, T('Alles, was in der Auswahl sichtbar ist, sieht dein Gegenüber – auch Benachrichtigungen, Passwörter und andere Fenster oder Chats.')));
    box.appendChild(el('p', 'cl-hint-s', T('Nichts wird aufgezeichnet oder auf dem Server gespeichert. Teile am besten nur ein Fenster oder einen Tab.')));
    var lab = el('label', 'cl-hint-c'), cb = el('input'); cb.type = 'checkbox'; lab.appendChild(cb); lab.appendChild(document.createTextNode(' ' + T('Nicht mehr anzeigen')));
    box.appendChild(lab);
    var row = el('div', 'cl-hint-b');
    var close = function () { box.remove(); if (c.ui) c.ui.hint = null; };
    row.appendChild(btn(T('Abbrechen'), 'cl-pill cl-hint-no', close));
    row.appendChild(btn(T('Bildschirm wählen'), 'cl-pill cl-hint-go', function () {
      if (cb.checked) { try { localStorage.setItem('saga-screen-hint', '0'); } catch (e) { /* privat */ } }
      close(); if (C === c && !c.done) pickScreen(c);
    }));
    box.appendChild(row);
    c.ui.w.appendChild(box); c.ui.hint = box;
    var go = box.querySelector('.cl-hint-go'); if (go) go.focus({ preventScroll: true });
  }
  function displayMedia() {
    var md = navigator.mediaDevices;
    // Systemton (Chromium: Tab-Ton, unter Windows/ChromeOS auch der ganze Systemton) - wo der Browser es kann
    var full = { video: { frameRate: { max: 15 }, width: { max: 1920 }, height: { max: 1080 } },
                 audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, restrictOwnAudio: true },
                 systemAudio: 'include', selfBrowserSurface: 'exclude', surfaceSwitching: 'include', monitorTypeSurfaces: 'include' };
    return md.getDisplayMedia(full).catch(function (e) {
      if (e && e.name === 'TypeError') return md.getDisplayMedia({ video: true });    // aeltere Browser: ohne Zusatzwuensche
      throw e;
    });
  }
  function pickScreen(c) {
    displayMedia().then(function (s) {
      if (C !== c || c.done || c.screen) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
      var v = s.getVideoTracks()[0]; if (!v) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
      c.screen = v; c.scrStream = s; c.shareStarts += 1;
      c.screen.onended = function () { if (C === c && c.screen === v) stopScreen(c); };
      try { c.screen.contentHint = 'detail'; } catch (e) { /* aeltere Browser */ }
      var at = s.getAudioTracks()[0];
      if (at) mixOn(c, at);
      c.scrMode = canScreen2(c) && c.established ? 'track' : 'replace';
      if (c.scrMode === 'track') screenTrackOn(c);
      else videoSender(c).replaceTrack(c.screen).then(function () { delete c.rates.video; applyRates(c); });
      drawMedia(c); drawCtl(c); drawShare(c);
      if (c.scrMode === 'track') stateLater(c); else stateOut(c);
    }).catch(function (e) { if (e && e.name !== 'NotAllowedError' && e.name !== 'AbortError') HK.toast(T('Bildschirm teilen nicht möglich: {err}', { err: e.message })); });
  }
  // Betriebsart B: vorhandene Bildschirm-m-Zeile wiederverwenden (eigene oder vom Gegenueber angelegte, Rolle "screen") -
  // so waechst das SDP nicht bei jedem Teilen; sonst neuen Transceiver anlegen. Beides loest negotiationneeded aus.
  function screenTrackOn(c) {
    var tr = c.scrTr;
    if (!tr) {
      c.pc.getTransceivers().some(function (x) {
        if (x !== c.camTr && x.mid != null && c.peerTracks[x.mid] === 'screen' && x.receiver.track && x.receiver.track.kind === 'video') { tr = x; return true; }
        return false;
      });
    }
    if (tr) {
      c.scrTr = tr;
      tr.sender.replaceTrack(c.screen).then(function () { delete c.rates.screen; applyRates(c); });
      if (tr.sender.setStreams) tr.sender.setStreams(c.scrStream);
      tr.direction = 'sendrecv';
    } else {
      c.scrTr = c.pc.addTransceiver(c.screen, { direction: 'sendrecv', streams: [c.scrStream] });
      delete c.rates.screen;
    }
    delete c.rates.video;                                          // Kamera daneben drosseln
    applyRates(c);
  }
  function stopScreen(c) {
    var v = c.screen;
    if (v) { v.onended = null; v.stop(); }
    if (c.scrStream) c.scrStream.getTracks().forEach(function (t) { t.stop(); });
    mixOff(c);
    var mode = c.scrMode;
    c.screen = null; c.scrStream = null; c.scrMode = ''; c.camPaused = false;
    if (c.pc && mode === 'track' && c.scrTr) {
      // Spur weg, Richtung "nur empfangen": teilt das Gegenueber nicht, ist die m-Zeile danach inaktiv (bleibt fuer das
      // naechste Teilen bestehen). Die Richtungsaenderung loest die Neuverhandlung aus.
      c.scrTr.sender.replaceTrack(null);
      c.scrTr.direction = 'recvonly';
      delete c.rates.screen; delete c.rates.video;
      if (c.applied) delete c.applied.screen;
      applyRates(c);
      stateLater(c);
    } else {
      var snd = c.pc && videoSender(c);
      if (snd) snd.replaceTrack(c.cam || null).then(function () { delete c.rates.video; applyRates(c); });
    }
    drawMedia(c); drawCtl(c); drawShare(c);
    if (!c.stateDue) stateOut(c);
  }
  // Zustand nach der Neuverhandlung senden (screen_mid steht erst nach setLocalDescription fest); offerNow schickt ihn
  // direkt nach dem Angebot, sonst spaetestens nach 3 s
  function stateLater(c) {
    c.stateDue = true;
    setTimeout(function () { if (C === c && c.stateDue) { c.stateDue = false; stateOut(c); } }, 3000);
  }
  // Systemton in die Tonspur mischen (Mikrofon + Ton der Quelle). Stumm schaltet weiter nur das Mikrofon.
  function mixOn(c, at) {
    var ctx = audioCtx(), snd = c.pc && audioSender(c), mic = c.local && c.local.getAudioTracks()[0];
    if (!ctx || !snd || !ctx.createMediaStreamDestination) return;
    try {
      var dst = ctx.createMediaStreamDestination(), nodes = [];
      if (mic) { var a = ctx.createMediaStreamSource(new MediaStream([mic])); a.connect(dst); nodes.push(a); }
      var b = ctx.createMediaStreamSource(new MediaStream([at])); b.connect(dst); nodes.push(b);
      var out = dst.stream.getAudioTracks()[0];
      c.mix = { dst: dst, nodes: nodes, out: out, src: at };
      snd.replaceTrack(out).then(function () { delete c.rates.audio; applyRates(c); });
    } catch (e) { c.mix = null; }
  }
  function mixOff(c) {
    var m = c.mix; if (!m) return;
    c.mix = null;
    var snd = c.pc && audioSender(c), mic = c.local && c.local.getAudioTracks()[0];
    if (snd) snd.replaceTrack(mic || null).then(function () { delete c.rates.audio; applyRates(c); }).catch(function () {});
    m.nodes.forEach(function (n) { try { n.disconnect(); } catch (e) { /* egal */ } });
    try { m.out.stop(); } catch (e) { /* egal */ }
  }
  function hangup(c) {
    if (C !== c) return;
    if (c.established && peerCan(c, 'bye') && !c.ending) sendSignal(c, { t: 'bye', reason: 'hangup' });
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
      : r === 'timeout' ? (c.role === 'caller' ? T('{name} hat nicht abgenommen.', { name: name })
        : !c.ready ? T('Der Anruf ist abgelaufen, bevor Mikrofon/Kamera freigegeben waren – bitte zurückrufen.') : T('Verpasster Anruf.'))
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
    clearInterval(c.alive); clearInterval(c.statsT); clearTimeout(c.outT); clearTimeout(c.connT); clearTimeout(c.failT); clearTimeout(c.discT);
    pipClose(c); mediaSession(null);
    stopTone(); if (kind !== 'error' && c.ready) playTone('end');
    mixOff(c);
    [c.local, c.screen && { getTracks: function () { return [c.screen]; } }, c.scrStream].forEach(function (s) { if (s) s.getTracks().forEach(function (t) { t.stop(); }); });
    if (c.cam) c.cam.stop();
    try { if (c.pc) c.pc.close(); } catch (e) { /* schon zu */ }
    if (C === c) C = null;
    if (c.ui) {
      status(c, msg || '', kind === 'error');
      // vorbei: keine Laufzeit/Verbindungsangaben und kein schwarzes Eigenbild mehr
      c.ui.info.textContent = ''; c.ui.un.hidden = true; if (c.ui.sink) c.ui.sink.remove();
      c.ui.lv.srcObject = null; c.ui.lv.hidden = true; c.ui.w.classList.remove('has-video', 'screen', 'peer-muted', 'peer-screen', 'has-small');
      c.ui.rv2.srcObject = null; c.ui.share.hidden = true; if (c.ui.hint) { c.ui.hint.remove(); c.ui.hint = null; }
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
    // E3: zweites Fernbild (klein: Kamera neben dem Bildschirm, nach dem Tauschen der Bildschirm) - Klick tauscht
    var rv2 = el('video', 'cl-remote2'); rv2.autoplay = true; rv2.muted = true; rv2.playsInline = true; rv2.setAttribute('playsinline', '');
    rv2.title = T('Ansicht tauschen'); rv2.addEventListener('click', function () { swapView(c); });
    stage.appendChild(rv); stage.appendChild(ra); stage.appendChild(who); stage.appendChild(rv2); stage.appendChild(lv);
    var top = el('div', 'cl-top'), info = el('div', 'cl-info'), st = el('div', 'cl-status'); st.setAttribute('role', 'status');
    var mini = btn('▭', 'cl-mini cl-shrink', function () { w.classList.toggle('small'); }, T('Kleiner/größer'));
    // 2.4: Bild-im-Bild - nur, wo der Browser es kann (Document-PiP mit eigenen Knoepfen, sonst Video-PiP)
    var pipB = btn(icon('pip'), 'cl-mini cl-pip', function () { togglePip(c); }, T('Bild-im-Bild'));
    pipB.hidden = true;
    rv.addEventListener('enterpictureinpicture', function () { pipSet(c, true); });
    rv.addEventListener('leavepictureinpicture', function () { if (!c.pipWin) pipSet(c, false); });
    top.appendChild(info); top.appendChild(pipB); top.appendChild(mini);
    var ctl = el('div', 'cl-ctl');
    var un = btn(T('Ton einschalten'), 'cl-pill cl-unmute', function () { playAudio(c); }, T('Der Browser hat den Ton blockiert – tippen, um ihn einzuschalten'));
    un.hidden = true;
    // E3: Leiste "Du teilst deinen Bildschirm" (rot) bzw. "… teilt den Bildschirm" (Empfaenger) - immer sichtbar beim Teilen
    var share = el('div', 'cl-share'); share.setAttribute('role', 'status'); share.hidden = true;
    w.appendChild(stage); w.appendChild(top); w.appendChild(share); w.appendChild(un); w.appendChild(st); w.appendChild(ctl);
    document.body.appendChild(w);
    c.ui = { w: w, rv: rv, rv2: rv2, ra: ra, lv: lv, who: who, info: info, st: st, ctl: ctl, un: un, pipB: pipB, share: share, hint: null };
    mediaSession(c);
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
    if (!c.ui || c.done) return;
    var i = c.ui.info; i.textContent = '';
    if (!c.ready && !c.connected) return;
    i.appendChild(el('span', 'cl-lock', '🔒 ' + (c.verified ? T('Ende-zu-Ende verschlüsselt') : T('verschlüsselt'))));
    if (c.verified) i.lastChild.title = T('Medien mit DTLS-SRTP verschlüsselt; der Schlüssel-Fingerabdruck ist mit dem Chat-Schlüssel der Gegenseite signiert und geprüft.');
    if (c.connected) {
      i.appendChild(el('span', null, dur((Date.now() - c.t0) / 1000)));
      if (c.path) i.appendChild(el('span', 'cl-path', c.path === 'relay' ? T('über Relais (TURN)') : T('direkt')));
      if (c.q) { var qq = el('span', 'cl-q cl-q-' + c.q, c.q === 'good' ? T('gute Verbindung') : c.q === 'mid' ? T('mittlere Verbindung') : T('schwache Verbindung')); i.appendChild(qq); }
      // stumm beim Gegenueber: auch bei laufendem Video sichtbar (der Name mit 🔇 ist dann ausgeblendet)
      if (c.remoteState.mic === false) i.appendChild(el('span', 'cl-snd cl-peer-muted', '🔇 ' + T('{name} ist stummgeschaltet', { name: c.peer.name || c.peer.username || '' })));
      else if (c.snd === 'ok') i.appendChild(el('span', 'cl-snd', T('Ton: empfangen')));
      else if (c.snd === 'none' || c.snd === 'blocked') {
        var sn = el('span', 'cl-snd cl-snd-no', c.snd === 'blocked' ? T('Ton: blockiert') : T('Ton: kein Ton'));
        sn.title = c.snd === 'blocked' ? T('Der Browser hat den Ton blockiert – tippen, um ihn einzuschalten') : T('Vom Gegenüber kommt kein Ton an.');
        i.appendChild(sn);
      }
    }
    if (c.ui.un) c.ui.un.hidden = !c.audioBlocked;
  }
  function drawMedia(c) {
    if (!c.ui || c.done) return;
    var bigT = c.ui.rv.srcObject && c.ui.rv.srcObject.getVideoTracks()[0];
    var rvid = !!(bigT && bigT.readyState === 'live' && !bigT.muted);
    var show = rvid && (c.gotState ? c.remoteState.cam || c.remoteState.screen : true);
    var smT = c.ui.rv2.srcObject && c.ui.rv2.srcObject.getVideoTracks()[0];
    var small = !!(show && smT && smT.readyState === 'live' && !smT.muted);
    c.ui.w.classList.toggle('has-small', small);
    c.ui.w.classList.toggle('peer-screen', !!(show && c.remoteState.screen));
    if (small && c.ui.rv2.paused) c.ui.rv2.play().catch(function () {});
    c.ui.w.classList.toggle('has-video', show);
    c.ui.w.classList.toggle('peer-muted', c.remoteState.mic === false);
    var mine = c.screen || c.cam;
    if (mine) { var s = new MediaStream([mine]); if (!c.ui.lv.srcObject || c.ui.lv.srcObject.getTracks()[0] !== mine) c.ui.lv.srcObject = s; }
    else c.ui.lv.srcObject = null;
    c.ui.lv.hidden = !mine;
    c.ui.w.classList.toggle('screen', !!c.screen);
    if (show && c.ui.rv.srcObject && c.ui.rv.paused) c.ui.rv.play().catch(function () {});
    pipBtn(c, show);
    pipDraw(c);
    drawShare(c);
    playAudio(c);
  }
  function swapView(c) { if (C !== c || !c.remoteState.screen) return; c.swap = !c.swap; bindRemote(c); drawMedia(c); }
  // Leiste beim Teilen: eigene (rot, mit "Beenden") hat Vorrang; beim Empfaenger Name und - mit Kamera daneben - Tauschen
  function drawShare(c) {
    var b = c.ui && c.ui.share; if (!b || c.done) return;
    b.textContent = '';
    b.classList.toggle('me', !!c.screen);
    if (c.screen) {
      b.appendChild(el('span', 'cl-share-dot'));
      b.appendChild(el('span', 'cl-share-t', c.mix ? T('Du teilst deinen Bildschirm und den Ton') : T('Du teilst deinen Bildschirm')));
      if (c.camPaused) b.appendChild(el('span', 'cl-share-s', T('Kamera pausiert (langsame Verbindung)')));
      b.appendChild(btn(T('Teilen beenden'), 'cl-pill cl-share-stop', function () { if (C === c && c.screen) stopScreen(c); }, T('Teilen beenden')));
      b.hidden = false;
    } else if (c.remoteState.screen && c.connected) {
      var name = (c.peer && (c.peer.name || c.peer.username)) || '';
      b.appendChild(el('span', 'cl-share-t', T('{name} teilt den Bildschirm', { name: name })));
      if (c.ui.w.classList.contains('has-small')) b.appendChild(btn(icon('swap'), 'cl-mini cl-swap', function () { swapView(c); }, T('Ansicht tauschen')));
      b.hidden = false;
    } else b.hidden = true;
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
    pipBtn(c, c.ui.w.classList.contains('has-video'));
    pipDraw(c);
  }

  // --- 2.4: Bild-im-Bild -----------------------------------------------------------------------------------------------
  // Document-PiP (Chromium): eigenes kleines Fenster mit Fernbild, Stumm, Zurueck, Auflegen. Sonst Video-PiP des Fernbilds
  // (Chrome/Edge/Safari; Knoepfe ueber Media Session, wo es geht). Firefox: kein API -> Knopf ausgeblendet. Im PiP und bei
  // verstecktem Tab bittet der Client das Gegenueber per state.want um ein kleines Bild (spart Bandbreite und Akku).
  function pipKind() {
    if (window.documentPictureInPicture && typeof window.documentPictureInPicture.requestWindow === 'function') return 'doc';
    if (document.pictureInPictureEnabled && window.HTMLVideoElement && typeof HTMLVideoElement.prototype.requestPictureInPicture === 'function') return 'video';
    return '';
  }
  // Knopf nur, wo der Browser Bild-im-Bild kann (Video-PiP braucht ein Fernbild)
  function pipBtn(c, show) {
    var pk = pipKind(), b = c.ui && c.ui.pipB; if (!b) return;
    b.hidden = !pk || (pk === 'video' && !show && !c.pip);
    b.classList.toggle('on', !!c.pip); b.setAttribute('aria-pressed', String(!!c.pip));
    b.title = c.pip ? T('Bild-im-Bild beenden') : T('Bild-im-Bild'); b.setAttribute('aria-label', b.title);
  }
  function pipSet(c, on) {
    on = !!on;
    if (c.pip === on) return;
    c.pip = on;
    if (c.ui && !c.done) drawMedia(c);
    stateOut(c);
  }
  function togglePip(c) { if (c.pip) pipClose(c); else pipOpen(c); }
  function pipOpen(c) {
    var k = pipKind();
    if (!k || !c.ui || c.done) return Promise.resolve(false);
    if (k === 'doc') return window.documentPictureInPicture.requestWindow({ width: 360, height: 270 }).then(function (w) { pipDoc(c, w); return true; },
      function () { return pipVideo(c); });
    return pipVideo(c);
  }
  function pipVideo(c) {
    var v = c.ui && c.ui.rv;
    if (!v || !v.srcObject || v.readyState < 1 || !v.requestPictureInPicture) return Promise.resolve(false);
    return v.requestPictureInPicture().then(function () { pipSet(c, true); return true; }, function () { return false; });
  }
  function pipDoc(c, w) {
    if (C !== c || c.done) { w.close(); return; }
    c.pipWin = w;
    var d = w.document;
    [].forEach.call(document.querySelectorAll('link[rel="stylesheet"]'), function (l) {
      var n = d.createElement('link'); n.rel = 'stylesheet'; n.href = l.href; d.head.appendChild(n);
    });
    d.title = T('Anruf'); d.documentElement.className = document.documentElement.className;
    if (document.documentElement.dataset.theme) d.documentElement.dataset.theme = document.documentElement.dataset.theme;
    d.body.className = 'cl-pipdoc';
    var mk = function (tag, cls, txt) { var x = d.createElement(tag); if (cls) x.className = cls; if (txt) x.textContent = txt; return x; };
    var stage = mk('div', 'cl-pip-stage'), v = mk('video', 'cl-pip-v'), who = mk('div', 'cl-pip-who', (c.peer && (c.peer.name || c.peer.username)) || '');
    v.autoplay = true; v.muted = true; v.playsInline = true; v.setAttribute('playsinline', '');
    var ctl = mk('div', 'cl-pip-ctl');
    stage.appendChild(v); stage.appendChild(who); d.body.appendChild(stage); d.body.appendChild(ctl);
    c.pipUi = { d: d, v: v, stage: stage, ctl: ctl };
    w.addEventListener('pagehide', function () { if (c.pipWin === w) { c.pipWin = null; c.pipUi = null; pipSet(c, false); } });
    pipSet(c, true);
    pipDraw(c);
  }
  function pipDraw(c) {
    var u = c.pipUi; if (!u || !c.ui) return;
    var src = c.ui.rv.srcObject, vid = c.ui.w.classList.contains('has-video');
    if (u.v.srcObject !== src) { u.v.srcObject = src; if (src) u.v.play().catch(function () {}); }
    u.stage.classList.toggle('has-video', vid);
    u.ctl.textContent = '';
    var m = btn(icon(c.mic ? 'mic' : 'micOff'), 'cl-round' + (c.mic ? '' : ' off'), function () { toggleMic(c); }, c.mic ? T('Stummschalten') : T('Mikrofon einschalten'));
    m.setAttribute('aria-pressed', String(!c.mic));
    u.ctl.appendChild(u.d.adoptNode(m));
    u.ctl.appendChild(u.d.adoptNode(btn(icon('back'), 'cl-round', function () { pipClose(c); try { window.focus(); } catch (e) { /* egal */ } }, T('Zurück zum Anruffenster'))));
    u.ctl.appendChild(u.d.adoptNode(btn(icon('phone'), 'cl-round cl-red', function () { hangup(c); }, T('Auflegen'))));
  }
  function pipClose(c) {
    var w = c.pipWin;
    c.pipWin = null; c.pipUi = null;
    if (w) { try { w.close(); } catch (e) { /* schon zu */ } }
    if (c.ui && document.pictureInPictureElement && document.pictureInPictureElement === c.ui.rv && document.exitPictureInPicture) {
      document.exitPictureInPicture().catch(function () {});
    }
    pipSet(c, false);
  }
  // Media Session: Stumm/Auflegen in der Video-PiP-Ansicht, automatisches Bild-im-Bild beim Tab-Wechsel (Chrome)
  function mediaSession(c) {
    var ms = navigator.mediaSession; if (!ms || !ms.setActionHandler) return;
    var set = function (a, fn) { try { ms.setActionHandler(a, fn); } catch (e) { /* Browser kennt die Aktion nicht */ } };
    set('togglemicrophone', c ? function () { if (C === c) toggleMic(c); } : null);
    set('hangup', c ? function () { if (C === c) hangup(c); } : null);
    set('enterpictureinpicture', c ? function () { if (C === c && !c.pip) pipOpen(c); } : null);
    try { if (c && ms.setMicrophoneActive) ms.setMicrophoneActive(c.mic); } catch (e) { /* egal */ }
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
    var on = !!(chat && chat.kind === 'dm' && cfg && cfg.enabled && window.RTCPeerConnection && !(chat.v === 2 && (chat.request || chat.readonly)));
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
                 audio: Object.assign({ state: C.snd || '', blocked: !!C.audioBlocked }, C.audio || {}),
                 // 2.4 (saga-call v3)
                 v3: { caps: myCaps(), peerCaps: C.peerCaps, established: C.established, pc: C.peerDevice ? pcId(C) : '', polite: C.peerDevice ? polite(C) : null,
                       n: C.n, remoteN: C.remoteN, restarts: C.restarts, tries: C.tries || 0, restarting: C.restarting, net: C.net, pip: C.pip, pipKind: pipKind(),
                       wantSent: C.wantSent, rates: C.applied || {}, sent: C.sent,
                       signaling: C.pc ? C.pc.signalingState : '', ice: C.pc ? C.pc.iceConnectionState : '', ufrag: C.pc ? remoteUfrag(C) : null } } : null;
  }, ringing: function () { return Object.keys(RINGS); },
  // 2.4 E3: Bildschirm - Betriebsart, eigene/fremde m-Zeilen, was gross und klein gezeigt wird
  screen: function () {
    var c = C; if (!c) return null;
    var big = c.ui && c.ui.rv.srcObject && c.ui.rv.srcObject.getVideoTracks()[0], sm = c.ui && c.ui.rv2.srcObject && c.ui.rv2.srcObject.getVideoTracks()[0];
    var rvt = remoteVideo(c), role = function (t) { return !t ? '' : t === rvt.screenAny ? 'screen' : t === rvt.camAny ? 'cam' : '?'; };
    var mt = myTracks(c);
    return { mode: c.scrMode, sharing: !!c.screen, caps: canScreen2(c), mine: mt.tracks, msid: mt.msid, screenMid: myScreenMid(c),
             hint: c.screen ? c.screen.contentHint : '', audio: !!c.mix, camPaused: !!c.camPaused, starts: c.shareStarts,
             scrDir: c.scrTr ? c.scrTr.direction : '', scrCur: c.scrTr ? c.scrTr.currentDirection : '',
             mlines: c.pc && c.pc.localDescription ? (c.pc.localDescription.sdp.match(/^m=/gm) || []).length : 0,
             peerTracks: c.peerTracks, remoteMid: c.remoteState.screenMid, big: role(big), small: role(sm), swap: c.swap,
             bar: c.ui && !c.ui.share.hidden ? c.ui.share.textContent : '', hintOpen: !!(c.ui && c.ui.hint),
             audioTrack: c.pc && audioSender(c) && audioSender(c).track ? (c.mix && audioSender(c).track === c.mix.out ? 'mix' : 'mic') : '' };
  },
  // 2.4: fuer Tests und die Diagnose - ICE neu aushandeln (wie bei einem Netzwechsel), Bild-im-Bild, Regeltabelle
  restartIce: function () { return restartIce(C); }, pip: function (on) { if (!C) return Promise.resolve(false); return on === false ? (pipClose(C), Promise.resolve(true)) : pipOpen(C); },
  rules: rules, limits: { restartMax: RESTART_MAX, discS: DISC_S, failS: FAIL_S } };
})();
