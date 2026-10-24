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
// 2.4 (E4/E5, Raum, Vertrag docs/APP-API.md "Anrufe: Mehrgeraete und Gruppen"): ein Anruf kann mehrere PeerConnections
// haben (Mesh: je Geraete-Paar eine, pc = sortiertes Paar, wer hinzukommt bietet allen an). C ist der Raum und zugleich die
// erste Verbindung; jede weitere Verbindung ("Link") ist ein eigenes Objekt mit eigener PeerConnection, das die Felder des
// Raums (eigene Spuren, Bildschirm, Stumm, Fenster) beim Raum liest und schreibt (SHARED). Weiteres eigenes Geraet:
// "Hinzufuegen" (Begleitgeraet ohne Mikrofon und Ton) bzw. "Hierher uebernehmen" (das alte Geraet verlaesst den Anruf, ohne
// Abbruch). Gruppenanrufe in Gruppen-, freien und Seiten-Chats mit Kachelansicht, Sprecher-Hervorhebung ueber den
// Audio-Pegel und Mesh-Bitraten (§1.3). Jedes neue Geraet wird gegen die signierte Geraeteliste seiner Person geprueft.
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
  // multi (E4/E5) = Raum: weitere Geraete/Personen (join/leave, mehrere PeerConnections) - braucht reneg und multi am Server
  // horn (2.5) = Gruppenanrufe ueber einen Gjallarhorn-Knoten mit Frame-E2E - nur mit Server-Faehigkeit horn und Encoded Transform
  var CAPS_ALL = ['reneg', 'want', 'bye', 'screen2', 'multi', 'horn'];
  function feat(x) { return !!(cfg && (cfg.features || []).indexOf(x) >= 0); }
  function myCaps() {
    return CAPS_ALL.filter(function (x) {
      return x === 'reneg' ? feat('reneg') : x === 'screen2' ? feat('reneg') && feat('screen2') : x === 'multi' ? feat('reneg') && feat('multi')
        : x === 'horn' ? feat('reneg') && feat('multi') && feat('horn') && hornCan() : true;
    });
  }
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
    swap: ['M7 4L3 8l4 4', 'M3 8h14', 'M17 20l4-4-4-4', 'M21 16H7'],
    spk: ['M11 5L6 9H2v6h4l5 4V5z', 'M15.5 8.5a5 5 0 0 1 0 7', 'M19 5a10 10 0 0 1 0 14'],
    spkOff: ['M11 5L6 9H2v6h4l5 4V5z', 'M23 9l-6 6', 'M17 9l6 6'],
    // 2.5: Kachel-/Sprecheransicht, Teilnehmende
    grid: ['M3 3h7v7H3z', 'M14 3h7v7h-7z', 'M3 14h7v7H3z', 'M14 14h7v7h-7z'],
    spkView: ['M3 3h18v12H3z', 'M3 18h4v3H3z', 'M10 18h4v3h-4z', 'M17 18h4v3h-4z'],
    users: ['M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2', 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M23 21v-2a4 4 0 0 0-3-3.9', 'M16 3.1a4 4 0 0 1 0 7.8'],
    // 2.5 (ANRUF-UI): Schloss und Verkleinern statt Emoji/Zeichen
    lock: ['M5 11h14a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1z', 'M8 11V7a4 4 0 0 1 8 0v4'],
    shrink: ['M4 14h6v6', 'M20 10h-6V4', 'M14 10l7-7', 'M3 21l7-7']
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
    if (d.group && !d.dm && (C || d.silent)) { runCheck(); return; }   // 2.5 1:1 ueber Knoten (dm): besetzt wie 1:1        // 2.4 Gruppe: im Gespraech bzw. grosse Gruppe nur die Leiste
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
    if (d.group && !d.dm) tx.appendChild(el('span', null, (video ? T('Gruppen-Videoanruf …') : T('Gruppenanruf …')) + (from.chat ? ' · ' + from.chat : '')));
    else tx.appendChild(el('span', null, video ? T('Videoanruf …') : T('Anruf …')));
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
    RINGS[d.call_id] = { box: box, d: d, loud: false, at: Date.now() };
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
    if (d.group) { joinHere(d.call_id, 'member', video); return; }   // 2.4: Gruppenanruf = beitreten
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
      if (live(c) && !c.local) status(c, video ? T('Bitte Kamera und Mikrofon im Browser erlauben – die Abfrage steht oben bzw. neben der Adresse.')
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
      shareStarts: 0,
      // 2.4 E4/E5: Raum - weitere Verbindungen (Geraet -> Link), Endpunkte laut Server, Art, Begleitgeraet, Uebernahme,
      // Signale unbekannter Geraete (bis ihr Link steht), Schluessel je Person, Mitglieder (Gruppe)
      room: null, links: {}, legs: [], kind: 'dm', companion: false, ho: null, pend: {}, keysBy: {}, members: null, tile: null, gone: false,
      spkOut: true, level: 0 }, o);
  }
  // Felder des Raums, die ein Link beim Raum liest und schreibt (alles andere hat jeder Link selbst)
  var SHARED = ['id', 'chat_id', 'media', 'v2', 'local', 'cam', 'screen', 'scrStream', 'mix', 'mic', 'pip', 'pipWin', 'pipUi', 'ui', 'done',
    'ending', 'ready', 'alive', 'statsT', 'wantSent', 'shareStarts', 'seen', 'early', 'call', 'kind', 'companion', 'legs', 'links',
    'ho', 'pend', 'keysBy', 'members', 'spkOut', 'camPaused'];
  function newLink(R, dev, peer) {
    var L = newCall({ room: R, peerDevice: dev, role: 'caller', peer: peer || {} });
    SHARED.forEach(function (k) {
      delete L[k];
      Object.defineProperty(L, k, { get: function () { return R[k]; }, set: function (v) { R[k] = v; }, enumerable: true });
    });
    R.links[dev] = L;
    return L;
  }
  function roomOf(c) { return c && c.room ? c.room : c; }
  // Gehoert c (Raum oder Link) zum laufenden Anruf?
  function live(c) { return !!C && !!c && (c === C || c.room === C) && !c.gone; }
  // Verbindungen des Raums: der Raum selbst (wenn er eine hat) und alle Links
  function links(R) {
    R = roomOf(R); if (!R) return [];
    var o = R.pc && !R.gone ? [R] : [];
    Object.keys(R.links || {}).forEach(function (k) { var L = R.links[k]; if (L && !L.gone) o.push(L); });
    return o;
  }
  function linkOf(R, dev) { R = roomOf(R); return R.peerDevice === dev && !R.gone ? R : (R.links || {})[dev] || null; }
  function each(c, fn) { links(c).forEach(fn); }
  // Endpunkte im Raum (fuer Mesh-Bitraten): eigene Verbindungen + ich
  function endpoints(c) { var R = roomOf(c); return Math.max(2, links(R).length + 1); }
  // "Stille" Verbindung: eigenes Geraet bzw. Begleitgeraet ohne Ton, Kamera und Bildschirm - braucht keine eigene Kachel
  function quiet(L) {
    var x = legOf(roomOf(L), L.peerDevice), rs = L.remoteState || {};
    if (rs.cam || rs.screen) return false;
    return !!(L.peer && L.peer.me) || (!!x && x.role === 'companion' && (!L.gotState || rs.mic === false));
  }
  function shown(R) { return links(R).filter(function (L) { return !quiet(L); }); }
  // Kachelansicht: Gruppenanruf oder mehr als eine sichtbare Verbindung (1:1 mit Begleitgeraet bleibt Einzelansicht)
  function meshView(R) { R = roomOf(R); return !!R && (R.kind === 'group' || shown(R).length >= 2); }
  function myDev() { return bridge().device(); }
  function X() { return bridge().crypto(); }
  // 2.3: Gegenueber auf einem anderen Server (c.v2): saga-call/v2 mit foederierten Handles statt Benutzernamen
  function me(c) { return c.v2 ? bridge().myHandle() : bridge().me(); }
  function them(c) { return c.v2 ? c.peer.handle : c.peer.username; }
  // Schluessel einer Person (geprueft: Geraete-Zertifikate vom Identitaetsschluessel, TOFU wie im Chat), je Raum
  // zwischengespeichert - mehrere Geraete derselben Person (Begleitgeraet, Uebernahme) brauchen sie nur einmal
  // Foederiert (v2): Personen anderer Server ueber die geprueften Fremd-Schluessel, eigene und lokale wie im Chat
  function keysOf(c, p, fresh) {
    var v2 = !!(c.v2 && p.handle && !p.local && !p.me);
    var R = roomOf(c), k = (v2 ? 'h:' + p.handle : 'u:' + p.username);
    if (!R.keysBy) R.keysBy = {};
    if (!fresh && R.keysBy[k]) return R.keysBy[k];
    var pr = v2 ? bridge().peerKeysV2(p.handle, p.name) : bridge().peerKeys(p.username, p.name);
    R.keysBy[k] = pr.catch(function (e) { delete R.keysBy[k]; throw e; });
    return R.keysBy[k];
  }
  function peerKeys(c, fresh) {
    if (!fresh && c._keys) return c._keys;
    c._keys = keysOf(c, c.peer, fresh).then(function (k) {
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
    pc.onnegotiationneeded = function () { if (live(c) && c.established && canReneg(c) && !c.done) offerNow(c); };
    return pc;
  }
  function addLocal(c, pc, fromOffer) {
    var a = c.local.getAudioTracks()[0] || null, v = c.local.getVideoTracks()[0] || null;
    c.cam = v;
    drawCtl(c);                                        // Kamera-Knopf: beim Klingeln ohne Kamera gezeichnet (Bughunt 2)
    if (v && c === roomOf(c)) drawMedia(c);            // Eigenbild sofort (Web-Runde 2: Angerufene sah es erst nach ~2 min)
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
    if (b.t === 'state' && b.ready && b.replaces === myDev().id && c.peer && c.peer.me) { handoverOut(roomOf(c), b._from); return; }
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
    else if (b.t === 'offer' && !c.established && !c.pc && b._from === c.peerDevice) acceptOffer(c, b);
    else if (b.t === 'answer' && c.established && b._from === c.peerDevice) reAnswer(c, b);
    else if (b.t === 'state') {
      var w = b.want && typeof b.want === 'object' && ['off', 'small', 'large'].indexOf(b.want.video) >= 0 ? b.want.video : 'large';
      var sm = typeof b.screen_mid === 'string' && /^[A-Za-z0-9_-]{1,16}$/.test(b.screen_mid) ? b.screen_mid : null;
      var was = c.remoteState.screen;
      c.remoteState = { mic: b.mic !== false, cam: !!b.cam, screen: !!b.screen, pip: !!b.pip, want: w,
                        path: PATHS.indexOf(b.path) >= 0 ? b.path : '', screenMid: b.screen ? sm : null, audio: !!b.screen_audio };
      peerTracksIn(c, b);
      if (c.remoteState.screen && !was) c.swap = false;               // neues Teilen: Bildschirm gross
      c.gotState = true;
      if (c.room || links(c).length > 1) drawTiles(roomOf(c));        // 2.4: Begleitgeraet zeigt etwas -> ggf. Kacheln
      bindRemote(c); drawMedia(c); drawInfo(c); applyRates(c);
    } else if (b.t === 'bye' && c.established && b._from === c.peerDevice) {
      if (links(c).length > 1 || roomOf(c).kind === 'group') dropLink(c, '');          // 2.4: nur dieses Geraet geht
      else remoteEnded(c, { reason: 'hangup' });
    }
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
      if (!live(c) || pc.signalingState !== 'have-local-offer') return;
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
      if (!live(c)) return;
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
    if (live(c) && c.pc.signalingState === 'stable' && (x === 'connected' || x === 'completed')) onConn(c);
  }
  // ICE neu aushandeln (Netzwechsel WLAN <-> LTE, Verbindung weg). Nur mit einer 2.4-Gegenstelle; sonst wie bisher.
  // Hoechstens RESTART_MAX Versuche je Abbruch (zurueckgesetzt, sobald die Verbindung wieder steht); kommt sie nach
  // FAIL_S Sekunden nicht wieder, endet der Anruf wie bisher
  function restartIce(c) {
    if (!c || !live(c) || !c.pc || c.done || !c.established || !canReneg(c)) return false;
    if (c.restarting && Date.now() - c.restartAt < 6000) return true;
    if (c.tries >= RESTART_MAX) return false;
    c.restarting = true; c.restartAt = Date.now(); c.restarts += 1; c.tries = (c.tries || 0) + 1;
    status(c, T('Verbindung wird neu aufgebaut …'));
    if (!c.failT) c.failT = setTimeout(function () { c.failT = null; if (live(c) && c.restarting) iceFailed(c); }, FAIL_S * 1000);
    if (c.pc.restartIce) c.pc.restartIce();                       // -> negotiationneeded -> offerNow
    else offerNow(c, true);
    return true;
  }
  var netT = null;
  function netChanged() {
    clearTimeout(netT);
    netT = setTimeout(function () { var c = C; if (c && !c.done) each(c, function (L) { if (L.connected) restartIce(L); }); }, 800);
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
    sigRoute(c, d);
  }
  // 2.4: Signal an die passende Verbindung. 1:1 waehrend es klingelt: Antworten/Kandidaten aller Geraete des Gegenuebers
  // gehen an den Raum (wie bisher). Unbekanntes Geraet (Raum): merken und pruefen, ein Angebot legt eine Verbindung an.
  function sigRoute(R, d) {
    if (R.horn) { hornSig(R, d); return; }             // 2.5: Knoten-Anruf - nur fkey/fkey-ack
    var L = linkOf(R, d.from_device);
    if (!L && R.kind === 'dm' && R.role === 'caller' && R.pc && !R.peerDevice && !R.established && R.peerDevs[d.from_device]) L = R;
    if (L) { openSig(L, d).then(function (b) { if (live(L)) handle(L, b); }); return; }
    if (myCaps().indexOf('multi') < 0) return;
    var q = R.pend[d.from_device] = R.pend[d.from_device] || [];
    q.push(d); if (q.length > 80) q.shift();
    incoming(R, d.from_device);
  }

  // --- Anrufen -----------------------------------------------------------------------------------------------------
  // direct: 2.5 - nicht ueber den Knoten (Rueckfall nach 409 fallback)
  function start(chat, video, direct) {
    var b = bridge();
    if (!b) { HK.toast(T('Chats sind auf diesem Gerät noch nicht bereit.')); return; }
    if (C) { HK.toast(T('Du telefonierst gerade schon.')); return; }
    if (chat && chat.kind !== 'dm') {                  // 2.4 E5: Gruppen-, freie und Seiten-Chats
      var run = chat.call && chat.call.running;
      if (run && run.kind === 'group' && run.state !== 'ended') join(run.id, 'member', video);
      else startGroup(chat, video);
      return;
    }
    if (!chat || !chat.peer) return;
    if ((chat.v === 2 || chat.v === 3) && (chat.request || chat.readonly)) { HK.toast(T('Anrufe gehen erst, wenn beide die Unterhaltung angenommen haben.')); return; }
    if (!window.RTCPeerConnection) { HK.toast(T('Dieser Browser kann keine Anrufe (WebRTC fehlt).')); return; }
    // 2.5: "Alle Anrufe ueber Knoten" (horn_dm) - 1:1 als Raum-Anruf ueber einen Knoten; geht es nicht: direkt wie bisher
    if (!direct && cfg && cfg.horn_dm && myCaps().indexOf('horn') >= 0 && myCaps().indexOf('multi') >= 0) { startGroup(chat, video, true, true); return; }
    var c = C = newCall({ chat_id: chat.id, role: 'caller', media: video ? 'video' : 'audio', peer: chat.peer, v2: (chat.v === 2 || chat.v === 3) });
    audioCtx();
    win(c); status(c, T('Kamera und Mikrofon …'));
    config(true).then(function () {
      if (!cfg.enabled) throw new Error(T('Anrufe sind auf diesem Server abgeschaltet.'));
      return peerKeys(c);
    }).then(function (k) {
      if (!k.devices.length) throw new Error(T('{name} hat Chats noch auf keinem Gerät eingerichtet – Anrufe gehen erst danach.', { name: c.peer.name }));
      return mediaHint(c, video);
    }).then(function (s) {
      if (!live(c)) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
      c.local = s; c.media = s.getVideoTracks().length ? 'video' : 'audio'; drawMedia(c); sinkPick(c);
      c.pc = makePC(c); addLocal(c, c.pc, false);
      return c.pc.createOffer().then(function (o) { return c.pc.setLocalDescription(o); }).then(function () { return dial(c, false); });
    }).catch(function (e) { if (live(c)) fail(c, e.message); });
  }
  function dial(c, again) {
    status(c, T('Verbinde …'));
    c.id = c.id || X().hex(16);
    var sdp = c.pc.localDescription.sdp, me_ = myDev().id;
    c.n = 1;
    var body = function (id) { return JSON.stringify(Object.assign(sdpBody(c, 'offer', sdp, 1), { pc: pair(me_, id) })); };
    return api('POST', '/api/calls', { id: c.id, chat_id: c.chat_id, media: c.media, device: me_, signals: sealAll(c, Object.keys(c.peerDevs), body), caps: myCaps() })
      .then(function (r) {
        if (!live(c)) { api('POST', '/api/calls/' + c.id + '/end', { reason: 'hangup' }).catch(function () {}); return; }
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
      if (!live(c)) { s.getTracks().forEach(function (t) { t.stop(); }); throw null; }
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
      if (!live(c)) return;
      c.ready = true; c.t0 = 0; c.established = true;
      status(c, T('Verbinde …'));
      applyRemoteIce(c); flushIce(c); aliveOn(c); stateOut(c);
      var early = c.early.splice(0);
      early.forEach(function (d) { openSig(c, d).then(function (b) { if (live(c)) handle(c, b); }); });
      c.connT = setTimeout(function () { if (live(c) && !c.connected) iceFailed(c); }, 30000);
    }).catch(function (e) {
      if (!live(c) || e === null) return;
      fail(c, e.status === 409 ? T('Der Anruf ist schon beendet oder wurde auf einem anderen Gerät angenommen.') : e.message);
    });
  }

  // --- 2.4 E4/E5: Raum - beitreten, Mesh, Begleitgeraet, Uebernahme, Gruppen ---------------------------------------------
  // Vertrag: docs/APP-API.md "Anrufe: Mehrgeraete und Gruppen". Wer hinzukommt, bietet allen an (je Paar eine
  // PeerConnection); die anderen nehmen ohne Klingeln an, nachdem sie das Geraet gegen die signierte Geraeteliste seiner
  // Person geprueft haben. Der Server sieht keine SDP.
  var HO_S = 15;                                      // Uebernahme: so lange darf das neue Geraet brauchen (Konzept §5.3 c)
  function meName() { return bridge() ? bridge().me() : ''; }
  // Person eines Endpunkts (Teilnehmerliste des Servers) fuer Schluessel, Signatur und Anzeige
  function personOf(x) {
    var mine = !!x && x.who === meName();
    return { username: x.who, name: x.name || '', handle: mine ? (x.handle || (bridge() && bridge().myHandle()) || null) : x.handle || null,
             me: mine, local: mine || !x.host, host: x.host || '', avatar: x.avatar || null };
  }
  function legOf(R, dev) { var o = null; ((R && R.legs) || []).forEach(function (x) { if (x.device === dev) o = x; }); return o; }
  function myLeg(R) { return bridge() ? legOf(R, myDev().id) : null; }
  // Name des Chats zum Anruf - auch wenn gerade ein anderer Chat offen ist (Nr. 106: Kopf zeigte sonst "Gruppenanruf")
  function titleOf(cid) {
    var b = bridge(), c = b && b.cur();
    if (c && c.id === cid) return c.title || c.name || '';
    return (b && b.chatName && b.chatName(cid)) || '';
  }
  function blockText(b, n) {
    return b === 'old' ? T('Dafür braucht jemand im Anruf Saga 2.4.') : b === 'full' ? T('Der Anruf ist voll (höchstens {n} Geräte).', { n: n || '?' })
      : b === 'ended' ? T('Der Anruf ist schon beendet.') : b === 'none' ? T('Der Anruf ist noch nicht angenommen.') : T('Beitreten geht gerade nicht.');
  }
  // Teilnehmerliste uebernehmen (GET /api/calls/{id}, call.roster): Begleitgeraet ja/nein, Hinweise "ist hinzugekommen" /
  // "hat verlassen", Verbindungen zu Geraeten trennen, die nicht mehr dabei sind
  function rosterIn(R, legs, extra) {
    if (!Array.isArray(legs) || R.done) return;
    callT0(R, extra);
    // 2.4: rv = Stand der Liste - eine aeltere (kam nach einer neueren an) verwerfen
    var rv = extra && typeof extra.rv === 'number' ? extra.rv : null;
    if (rv !== null) { if (R.rv && rv < R.rv) return; R.rv = rv; }
    // 2.5 P10: Gruppenanruf ueber Servergrenzen ohne Knoten (ein Server kann keinen nutzen) - einmal klar sagen
    if (extra && extra.horn_mesh && !R.meshNote) { R.meshNote = true; HK.toast(extra.horn_mesh, null, null, 12000); }
    var old = {}; (R.legs || []).forEach(function (x) { old[x.device] = x; });
    var first = !R.legs, here = bridge() ? myDev().id : '', gone = (extra && extra.gone) || {};
    // 2.5 (Nr. 121): der fuehrende Server hat dieses Geraet ausgetragen (Notaus fuer Gjallarhorn-Knoten) - Medien sind weg,
    // also klar beenden statt mit eingefrorenem Bild und "guter Verbindung" weiterzulaufen
    if (here && gone[here] && !R.ending) {
      R.ending = true;
      finish(R, gone[here] === 'notaus' ? T('Du wurdest getrennt: Der Server des Anrufs hat den Notaus für Gjallarhorn-Knoten für deinen Server eingeschaltet.')
        : T('Du bist nicht mehr im Anruf.'), 'info');
      return;
    }
    // Dieses Geraet fehlt in der Liste, ein anderes eigenes ist dabei: es hat uebernommen und dieses abgemeldet
    if (here && R.inRoom && !R.ho && !R.ending && old[here] && !legs.some(function (x) { return x.device === here; })) {
      // R09: nur wenn das andere Geraet gerade die Fuehrung uebernommen hat (neu dabei bzw. vorher Begleitgeraet) - ein
      // Begleitgeraet, das Begleitgeraet bleibt, heisst nur: dieses Geraet ist kurz aus der Liste gefallen (Aussetzer)
      var other = legs.filter(function (x) {
        return x.me && x.device !== here && x.role !== 'companion' && (!old[x.device] || old[x.device].role === 'companion');
      })[0];
      if (other) {
        R.ending = true;
        finish(R, T('Anruf auf {dev} übernommen.', { dev: other.dev_name || T('anderes Gerät') }), 'info');
        return;
      }
    }
    R.legs = legs;
    if (extra && extra.limit) R.limit = extra.limit;
    var mine = myLeg(R);
    if (mine) { R.inRoom = true; if (!R.ho && !R.took) R.companion = mine.role === 'companion'; if (mine.role !== 'companion') R.took = false; }
    var now = {};
    legs.forEach(function (x) {
      now[x.device] = true;
      if (!first && R.inRoom && !old[x.device] && x.device !== here)
        HK.toast(T('{name} ({dev}) ist hinzugekommen', { name: x.name || '?', dev: x.dev_name || T('Gerät') }));
    });
    Object.keys(old).forEach(function (d) {
      if (!now[d] && d !== here && R.inRoom) HK.toast(gone[d] === 'notaus'
        ? T('{name} ({dev}) wurde getrennt (Notaus für Gjallarhorn-Knoten)', { name: old[d].name || '?', dev: old[d].dev_name || T('Gerät') })
        : T('{name} ({dev}) hat den Anruf verlassen', { name: old[d].name || '?', dev: old[d].dev_name || T('Gerät') }));
    });
    links(R).forEach(function (L) { if (L.peerDevice && !now[L.peerDevice] && L.established) dropLink(L, ''); });
    if (R.horn) { hornKeys(R); hornMap(R); var q0 = R.horn.X ? R.horn.q.splice(0) : []; q0.forEach(function (d) { hornSig(R, d); }); hornRunes(R); hornList(R); }
    drawTiles(R); drawCtl(R); drawInfo(R);
  }
  // Verbindung zu einem Geraet trennen (hat den Anruf verlassen bzw. Fehler nur auf dieser Verbindung)
  function dropLink(L, msg) {
    var R = roomOf(L);
    clearTimeout(L.outT); clearTimeout(L.connT); clearTimeout(L.failT); clearTimeout(L.discT);
    try { if (L.pc) L.pc.close(); } catch (e) { /* schon zu */ }
    if (L.tile) { L.tile.box.remove(); L.tile = null; }
    if (L === R) {                                    // erste Verbindung des Raums (1:1): der Raum selbst bleibt bestehen
      Object.assign(R, { pc: null, peerDevice: null, established: false, connected: false, remote: new MediaStream(), micTr: null, camTr: null,
        scrTr: null, scrMode: '', rates: {}, peerTracks: {}, rmid: {}, pcFixed: null, remoteIce: [], outIce: [], gotState: false,
        remoteState: { mic: true, cam: false, screen: false, pip: false, want: 'large', path: '' } });
    } else { L.gone = true; if (R.links[L.peerDevice] === L) delete R.links[L.peerDevice]; }
    if (msg) HK.toast(msg);
    if (!live(R)) return;
    if (R.ui) { R.ui.ra.srcObject = null; R.ui.rv.srcObject = null; R.ui.rv2.srcObject = null; }
    drawTiles(R); drawMedia(primary(R)); drawInfo(R); drawCtl(R);
    if (!links(R).length) status(R, R.kind === 'group' ? T('Warte auf die anderen …') : T('Verbinde …'));
  }
  // Signal eines Geraets ohne Verbindung: gehoert es zum Anruf (Liste des Servers) und zur Person (signierte Geraeteliste,
  // TOFU)? Dann Verbindung anlegen; das Angebot beantwortet handle() -> acceptOffer()
  function incoming(R, dev) {
    R.inc = R.inc || {}; R.bad = R.bad || {};
    if (R.inc[dev] || R.bad[dev] || !R.ready || R.done) return;
    R.inc[dev] = true;
    var x0 = legOf(R, dev);
    (x0 ? Promise.resolve(x0) : api('GET', '/api/calls/' + R.id + '?device=' + q(myDev().id)).then(function (r) {
      rosterIn(R, r.call.legs, r.call); return legOf(R, dev);
    })).then(function (x) {
      if (!x || !live(R) || linkOf(R, dev)) return;
      var L = newLink(R, dev, personOf(x));
      L.role = 'callee';
      // 2.4 (Testrunde W9): Schluessel der Person sind je Raum zwischengespeichert - ein Geraet, das sie WAEHREND des Anrufs
      // gekoppelt hat (Hinzufuegen/Hierher uebernehmen), steht dort noch nicht: dann die Liste einmal neu holen
      return peerKeys(L).then(function () { return L.peerDevs[dev] ? null : peerKeys(L, true); }).then(function () {
        if (!L.peerDevs[dev]) {                        // Geister-Geraet: nicht in der signierten Liste der Person
          R.bad[dev] = true; dropLink(L, T('Unbekanntes Gerät von {name} abgelehnt.', { name: x.name || '?' })); return;
        }
        var list = (R.pend[dev] || []).splice(0); delete R.pend[dev];
        return list.reduce(function (p, d) { return p.then(function () { return openSig(L, d).then(function (b) { if (live(L)) handle(L, b); }); }); }, Promise.resolve());
      });
    }).catch(function (e) { if (e && e.message) HK.toast(e.message); }).then(function () { delete R.inc[dev]; });
  }
  // Angebot eines hinzukommenden Geraets beantworten (ohne Klingeln: die Person ist schon im Anruf bzw. tritt bei)
  function acceptOffer(L, b) {
    if (!fpOk(b)) { securityFail(L); return; }
    L.fps = X().sdpFingerprints(b.sdp); L.peerCaps = capsOf(b); L.remoteN = typeof b.n === 'number' ? b.n : 1; peerTracksIn(L, b);
    if (typeof b.pc === 'string' && /^[0-9a-f:]{1,200}$/.test(b.pc)) L.pcFixed = b.pc;
    var pc = L.pc = makePC(L);
    pc.setRemoteDescription({ type: 'offer', sdp: b.sdp }).then(function () { return addLocal(L, pc, true); })
      .then(function () { return pc.createAnswer(); }).then(function (a) { return pc.setLocalDescription(a); }).then(function () {
        if (!live(L)) return;
        return sendSignal(L, sdpBody(L, 'answer', pc.localDescription.sdp, L.remoteN)).then(function () {
          L.established = true; applyRemoteIce(L); flushIce(L); stateOut(L);
          L.connT = setTimeout(function () { if (live(L) && !L.connected) iceFailed(L); }, 30000);
        });
      }).catch(function (e) { if (live(L)) dropLink(L, T('Verbindung nicht aufgebaut: {err}', { err: (e && e.message) || '?' })); });
    drawTiles(roomOf(L));
  }
  // Mit diesem Geraet einem laufenden Anruf beitreten. role: 'member' (Gruppe), 'companion' (Begleitgeraet: ohne Mikrofon
  // und Ton), 'handover' (Hierher uebernehmen; rep = bisheriges eigenes Geraet, sonst das eigene Hauptgeraet laut Liste)
  function join(id, role, video, rep) {
    if (!bridge()) { HK.toast(T('Chats sind auf diesem Gerät noch nicht bereit.')); return; }
    if (C) { if (C.id !== id) HK.toast(T('Du telefonierst gerade schon.')); return; }
    if (!window.RTCPeerConnection) { HK.toast(T('Dieser Browser kann keine Anrufe (WebRTC fehlt).')); return; }
    unring(id); hideRun(id);
    var R = C = newCall({ id: id, role: 'caller' });
    R.companion = role === 'companion';
    if (role !== 'member') { R.mic = false; R.spkOut = false; }   // Begleitgeraet/Uebernahme: erst still (Echo-Schutz)
    audioCtx(); win(R); status(R, T('Verbinde …'));
    var dev = myDev().id;
    config(true).then(function () {
      if (myCaps().indexOf('multi') < 0) throw new Error(T('Dafür braucht dieser Server Saga 2.4.'));
      return api('GET', '/api/calls/' + id + '?device=' + q(dev));
    }).then(function (r) {
      var k = r.call;
      R.call = k; R.chat_id = k.chat_id; R.media = k.media; R.kind = k.kind; R.v2 = k.v === 2; R.peer = k.peer; R.title = titleOf(k.chat_id);
      if (k.dm) { R.dm = true; R.title = (k.peer && k.peer.name) || R.title; }   // 2.5: 1:1 ueber Knoten
      (r.signals || []).forEach(function (s) { R.seen[s.seq] = true; });
      if (k.state === 'ended') throw new Error(T('Der Anruf ist schon beendet.'));
      if (!k.joinable) throw new Error(blockText(k.join_block, k.limit));
      R.legs = k.legs || [];
      if (role === 'handover') {
        var mine = R.legs.filter(function (x) { return x.who === meName() && x.device !== dev; });
        var main = mine.filter(function (x) { return x.role !== 'companion'; })[0] || mine[0];
        rep = rep || (main && main.device);
        if (!rep) role = 'member';
        else R.ho = { rep: rep };
      }
      drawPeer(R);
      return R.companion ? new MediaStream() : mediaHint(R, video);
    }).then(function (s) {
      if (!live(R)) { s.getTracks().forEach(function (t) { t.stop(); }); throw null; }
      R.local = s;
      s.getAudioTracks().forEach(function (t) { t.enabled = R.mic; });
      drawMedia(R); sinkPick(R);
      return R.call.topo === 'horn' ? hornJoin(R, role) : offerAll(R, role, rep, false);
    }).catch(function (e) {
      if (!live(R) || e === null) return;
      // Nr. 108/109: auch nach einem Fehler der Beitrittsanfrage selbst (z. B. 5xx) austragen - sonst bleibt ein Bein ohne
      // Person, und die Leiste "Gruppenanruf laeuft · Beitreten" fehlt, weil das Geraet als "dabei" gilt
      if (R.inRoom || R.joinSent) api('POST', '/api/calls/' + id + '/leave', { device: dev }).catch(function () {});
      finish(R, (e && e.message) || T('Beitreten ging nicht.'), 'error');
    });
  }
  // 2.5: Beitritt zu einem Anruf ueber einen Knoten - ohne Mesh-Angebote, die Antwort traegt die Kandidaten
  function hornJoin(R, role) {
    if (myCaps().indexOf('horn') < 0) return Promise.reject(new Error(T('Dafür braucht dieses Gerät Saga 2.5.')));
    hornNew(R, null);
    R.joinSent = true;
    var jb = { device: myDev().id, role: role === 'handover' && !R.ho ? 'member' : role, caps: myCaps(), dev_class: mobile ? 'mobile' : 'desktop' };
    if (role === 'handover' && R.ho) jb.replaces = R.ho.rep;
    return api('POST', '/api/calls/' + R.id + '/join', jb).then(function (r) {
      if (!live(R)) { api('POST', '/api/calls/' + R.id + '/leave', { device: myDev().id }).catch(function () {}); return; }
      R.ready = true; R.inRoom = true; R.call = r.call;
      rosterIn(R, r.call.legs, r.call);
      aliveOn(R);
      R.early.splice(0).forEach(function (d) { sigRoute(R, d); });
      if (R.ho) {                                      // Uebernahme: warten, bis der Knoten verbunden ist (hoCheck)
        R.hoT = setTimeout(function () { hoFail(R); }, HO_S * 1000);
        R.hoI = setInterval(function () { if (!R.ho || !live(R)) { clearInterval(R.hoI); return; } hoCheck(R); }, 300);
      }
      return hornGo(R, r.call.horn);
    }, function (e) {
      throw new Error(e.data && e.data.join_block ? (e.data.error || blockText(e.data.join_block, e.data.limit)) : e.message);
    });
  }
  // Je anderem Endpunkt eine PeerConnection mit Angebot, alles in einem POST /join. 409 mit neuer Liste: einmal neu.
  function offerAll(R, role, rep, again) {
    var me_ = myDev().id, sig = {};
    Object.keys(R.links).forEach(function (k) { var L = R.links[k]; try { if (L.pc) L.pc.close(); } catch (e) { /* zu */ } L.gone = true; if (L.tile) L.tile.box.remove(); });
    R.links = {};
    var others = (R.legs || []).filter(function (x) { return x.device !== me_; });
    return Promise.all(others.map(function (x) {
      var L = newLink(R, x.device, personOf(x));
      return peerKeys(L).then(function () {
        if (!L.peerDevs[x.device]) throw new Error(T('Unbekanntes Gerät von {name} im Anruf – Beitritt abgebrochen.', { name: x.name || '?' }));
        L.pc = makePC(L); addLocal(L, L.pc, false);
        return L.pc.createOffer().then(function (o) { return L.pc.setLocalDescription(o); }).then(function () {
          L.n = 1;
          sig[x.device] = seal(L, x.device, JSON.stringify(Object.assign(sdpBody(L, 'offer', L.pc.localDescription.sdp, 1), { pc: pair(me_, x.device) })));
        });
      });
    })).then(function () {
      var body = { device: me_, role: role, caps: myCaps(), signals: sig };
      if (rep && role === 'handover') body.replaces = rep;
      R.joinSent = true;
      return api('POST', '/api/calls/' + R.id + '/join', body);
    }).then(function (r) {
      if (!live(R)) { api('POST', '/api/calls/' + R.id + '/leave', { device: me_ }).catch(function () {}); return; }
      R.ready = true; R.inRoom = true; R.call = r.call;
      rosterIn(R, r.call.legs, r.call);
      status(R, links(R).length ? T('Verbinde …') : T('Warte auf die anderen …'));
      aliveOn(R);
      each(R, function (L) { flushIce(L); L.connT = setTimeout(function () { if (live(L) && !L.connected) iceFailed(L); }, 30000); });
      if (R.ho) R.hoT = setTimeout(function () { hoFail(R); }, HO_S * 1000);
      R.early.splice(0).forEach(function (d) { sigRoute(R, d); });
      drawTiles(R); drawCtl(R); drawInfo(R);
    }, function (e) {
      if (e.status === 409 && e.data && Array.isArray(e.data.legs) && !e.data.join_block && !again) { R.legs = e.data.legs; return offerAll(R, role, rep, true); }
      throw new Error(e.data && e.data.join_block ? (e.data.error || blockText(e.data.join_block, e.data.limit)) : e.message);
    });
  }
  // Uebernahme: sobald alle Verbindungen zu anderen Personen stehen, uebernimmt dieses Geraet Ton und Mikrofon und sagt
  // dem bisherigen Geraet Bescheid (state.ready + replaces) - das schaltet im selben Moment ab und verlaesst den Anruf
  function hoCheck(R) {
    R = roomOf(R);
    if (!R.ho || R.ho.done || !R.ready || R.done) return;
    if (R.horn) {
      if (!R.horn.cur) return;                         // 2.5: ueber einen Knoten - verbunden?
    } else {
      var need = links(R).filter(function (L) { return !(L.peer && L.peer.me); });
      if (!need.length || need.some(function (L) { return !L.connected; })) return;
    }
    var rep = R.ho.rep, A = R.horn ? null : linkOf(R, rep);
    R.ho.done = true; clearTimeout(R.hoT); clearInterval(R.hoI);
    R.companion = false; R.took = true;
    setMic(R, true); setSpk(R, true);
    // 2.4 (Web-Runde 2, M1): Kamera wie beim abgebenden Geraet (dessen letzter Zustand ueber die eigene Verbindung);
    // ohne Zustandsmeldung: wie beim Beitreten gewaehlt
    var camWas = A && A.gotState ? !!A.remoteState.cam : null;
    if (camWas === true && !R.cam) toggleCam(R);
    else if (camWas === false && R.cam) toggleCam(R);
    if (R.horn) {
      // ueber einen Knoten keine Direktverbindung zum alten Geraet: Bescheid ueber den Signalweg und zur Sicherheit
      // (aeltere App) dessen Platz kurz danach beim Server abmelden
      var o = {}; o[rep] = JSON.stringify({ t: 'state', ready: true, replaces: rep, mic: true, cam: !!R.cam, screen: !!R.screen });
      var id0 = R.id;
      hornSend(R, o).then(function () { setTimeout(function () { api('POST', '/api/calls/' + id0 + '/leave', { device: rep, reason: 'handover' }).catch(function () {}); }, 3000); });
    } else if (A && A.established) sendSignal(A, { t: 'state', ready: true, replaces: rep, mic: true, cam: camWas === null ? !!R.cam : camWas, screen: !!R.screen });
    else api('POST', '/api/calls/' + R.id + '/leave', { device: rep, reason: 'handover' }).catch(function () {});   // eigenes Geraet ohne Verbindung
    R.ho = null;
    drawCtl(R); drawInfo(R);
  }
  function hoFail(R) {
    if (!R.ho || R.ho.done || !live(R)) return;
    R.ho = null; clearInterval(R.hoI);
    api('POST', '/api/calls/' + R.id + '/leave', { device: myDev().id }).catch(function () {});
    R.ending = true;
    finish(R, T('Übernahme hat nicht geklappt – der Anruf läuft auf dem anderen Gerät weiter.'), 'error');
  }
  // Begleitgeraet -> Hauptgeraet ("Hierher übernehmen" im Anruffenster)
  function takeOver(R) {
    R = roomOf(R);
    if (!live(R) || R.ho) return;
    var mine = (R.legs || []).filter(function (x) { return x.who === meName() && x.device !== myDev().id; });
    var main = mine.filter(function (x) { return x.role !== 'companion'; })[0] || mine[0];
    if (!main) { R.companion = false; R.took = true; setMic(R, true); setSpk(R, true); drawCtl(R); drawInfo(R); return; }
    R.ho = { rep: main.device };
    hoCheck(R);
    if (R.ho) R.hoT = setTimeout(function () { if (R.ho && !R.ho.done) { R.ho = null; HK.toast(T('Übernahme hat nicht geklappt – der Anruf läuft auf dem anderen Gerät weiter.')); } }, HO_S * 1000);
  }
  // Dieses Geraet wurde von einem anderen eigenen Geraet uebernommen: still schalten, Bescheid geben, verlassen
  function handoverOut(R, from) {
    if (!live(R) || R.done) return;
    (R.local ? R.local.getAudioTracks() : []).forEach(function (t) { t.enabled = false; });
    R.spkOut = false; each(R, function (L) { var E = els(L); if (E && E.ra) E.ra.muted = true; });
    // erst "bye" an die anderen (danach nimmt der Server keine Signale dieses Geraets mehr an), dann verlassen
    var byes = [];
    each(R, function (L) { if (L.established && peerCan(L, 'bye') && L.peerDevice !== from) byes.push(sendSignal(L, { t: 'bye', reason: 'handover' })); });
    var x = legOf(R, from), id = R.id, me_ = myDev().id;
    Promise.race([Promise.all(byes), new Promise(function (r) { setTimeout(r, 1500); })]).then(function () {
      api('POST', '/api/calls/' + id + '/leave', { device: me_, reason: 'handover' }).catch(function () {});
    });
    R.ending = true;
    finish(R, T('Anruf auf {dev} übernommen.', { dev: (x && x.dev_name) || T('anderes Gerät') }), 'info');
  }
  // Gruppenanruf beginnen (Gruppen-, freie und Seiten-Chats): erst allein, alle anderen klingeln (call.invite)
  // dm (2.5): 1:1 im Direkt-Chat ueber einen Knoten - Anzeige wie 1:1 (Name des Gegenuebers), Rueckfall direkt
  function startGroup(chat, video, hinted, dm) {
    if (!window.RTCPeerConnection) { HK.toast(T('Dieser Browser kann keine Anrufe (WebRTC fehlt).')); return; }
    if (!dm && chat.call && chat.call.group === false) { HK.toast(chat.call.group_block || T('Gruppenanrufe gehen in diesem Chat nicht.')); return; }
    // 2.5: ohne bereiten Knoten erst der Hinweis auf die Mesh-Grenze (bekannte Einstellungen: vor dem Anruffenster; "Trotzdem
    // anrufen" ist wieder eine Bedienung, Ton und Kamera lassen sich danach freigeben)
    var big = cfg && cfg.limits && chat.members && chat.members.length > (video ? cfg.limits.endpoints_video : cfg.limits.endpoints_audio);
    if (!hinted && feat('horn') && big) {              // frisch fragen (lokal, wenige ms - die Bedienung gilt noch)
      config(true).then(function () { return hornMeshHint(chat, video); }, function () { return true; })
        .then(function (go) { if (go && !C) startGroup(chat, video, true); });
      return;
    }
    var R = C = newCall({ chat_id: chat.id, role: 'caller', kind: 'group', media: video ? 'video' : 'audio', v2: (chat.v === 2 || chat.v === 3) });
    R.title = chat.title || chat.name || '';
    if (dm) { R.dm = true; R.peer = chat.peer || {}; R.title = R.peer.name || R.title; }
    audioCtx(); win(R); status(R, T('Kamera und Mikrofon …'));
    config(true).then(function () {
      if (!cfg.enabled) throw new Error(T('Anrufe sind auf diesem Server abgeschaltet.'));
      if (myCaps().indexOf('multi') < 0) throw new Error(T('Gruppenanrufe brauchen Saga 2.4 auf diesem Server.'));
      return hinted ? true : hornMeshHint(chat, video); // 2.5: kein Knoten bereit (frisch abgefragt) -> Hinweis
    }).then(function (go) {
      if (!go) { R.ending = true; finish(R, '', 'info'); closeWin(R); throw null; }
      return mediaHint(R, video);
    }).then(function (s) {
      if (!live(R)) { s.getTracks().forEach(function (t) { t.stop(); }); throw null; }
      R.local = s; R.media = s.getVideoTracks().length ? 'video' : 'audio'; drawMedia(R); sinkPick(R);
      R.id = X().hex(16);
      return api('POST', '/api/calls', Object.assign({ id: R.id, chat_id: R.chat_id, media: R.media, device: myDev().id, caps: myCaps() },
                                                     dm ? { via: 'horn', dev_class: mobile ? 'mobile' : 'desktop' } : {}));
    }).then(function (r) {
      if (!live(R)) { api('POST', '/api/calls/' + r.call.id + '/end', { reason: 'hangup', device: myDev().id }).catch(function () {}); return; }
      if (r.call.topo === 'horn') hornNew(R, r.call.horn);   // 2.5: ueber einen Knoten
      R.ready = true; R.inRoom = true; R.call = r.call; R.v2 = r.call.v === 2;
      rosterIn(R, r.call.legs, r.call);
      status(R, T('Klingelt bei den anderen …')); playTone('back');
      aliveOn(R);
      R.early.splice(0).forEach(function (d) { sigRoute(R, d); });
      if (R.horn) hornGo(R, r.call.horn);
    }).catch(function (e) {
      if (!live(R) || e === null) return;
      var run = e && e.status === 409 && e.data && e.data.running;
      R.id = null;
      if (dm && e && e.status === 409 && e.data && e.data.fallback === 'direct') {   // 2.5: wie bisher direkt anrufen
        R.ending = true; finish(R, '', 'info'); closeWin(R); start(chat, video, true); return;
      }
      if (dm && e && e.status === 409 && e.data && e.data.busy) { finish(R, T('Besetzt – {name} telefoniert gerade.', { name: R.peer.name || '?' }), 'busy'); return; }
      if (run) { finish(R, '', 'error'); closeWin(R); join(run, 'member', video); return; }
      finish(R, (e && e.message) || T('Anruf beendet'), 'error');
    });
  }
  // Kachelansicht: je sichtbarer Verbindung eine Kachel (Bild bzw. Name, Geraet, stumm), Sprecher hervorgehoben
  function drawTiles(R) {
    R = roomOf(R);
    if (!R || !R.ui || R.done) return;
    if (R.horn) { hornDraw(R); return; }
    var mesh = meshView(R), ls = links(R);
    R.ui.w.classList.toggle('mesh', mesh);
    if (!mesh) {
      ls.forEach(function (L) { if (L.tile) { L.tile.box.remove(); L.tile = null; } });
      if (R.meshOn) { R.meshOn = false; R.ui.grid.textContent = ''; }
      var P = primary(R); if (P.pc) { bindRemote(P); drawMedia(P); }
      R.ui.w.classList.remove('tiles');
      return;
    }
    if (!R.meshOn) {                                   // Einzel- -> Kachelansicht: grosse Elemente frei machen
      R.meshOn = true;
      [R.ui.ra, R.ui.rv, R.ui.rv2].forEach(function (m) { m.srcObject = null; });
      R.ui.w.classList.remove('has-video', 'has-small', 'peer-screen', 'peer-muted');
      speakers(R);
    }
    ls.forEach(function (L) { if (!L.tile) makeTile(R, L); else drawTile(L); });
    R.ui.grid.dataset.n = String(Math.min(ls.length, 6));
    R.ui.w.classList.toggle('tiles', ls.length > 0);
    drawPeer(R);
  }
  function makeTile(R, L) {
    var vid = function (cls) { var v = el('video', cls); v.autoplay = true; v.muted = true; v.playsInline = true; v.setAttribute('playsinline', ''); return v; };
    var box = el('div', 'cl-tile'); box.setAttribute('role', 'listitem');
    var rv = vid('cl-tv'), rv2 = vid('cl-tv2'), ra = el('audio', 'cl-raudio'); ra.autoplay = true;
    rv2.title = T('Ansicht tauschen'); rv2.addEventListener('click', function () { L.swap = !L.swap; bindRemote(L); drawTile(L); });
    if (R.sinkId && ra.setSinkId) ra.setSinkId(R.sinkId).catch(function () {});
    var p = L.peer || {};
    var av = HK.avatarImg(el('span', 'cl-av', (p.name || '?').charAt(0).toUpperCase()), p.avatar, 56); av.setAttribute('aria-hidden', 'true');
    var lab = el('div', 'cl-tl'), nm = el('b', null, p.me ? T('Du') : shortName(p.name || p.username || '?')), dv = el('span', 'cl-td');
    lab.appendChild(nm); lab.appendChild(dv);
    var st = el('div', 'cl-tst'); st.hidden = true;
    [rv, rv2, ra, av, lab, st].forEach(function (x) { box.appendChild(x); });
    R.ui.grid.appendChild(box);
    L.tile = { box: box, rv: rv, rv2: rv2, ra: ra, av: av, nm: nm, dv: dv, st: st };
    bindRemote(L); drawTile(L);
  }
  function drawTile(L) {
    var t = L.tile; if (!t) return;
    var R = roomOf(L), x = legOf(R, L.peerDevice) || {}, rs = L.remoteState;
    var bigT = t.rv.srcObject && t.rv.srcObject.getVideoTracks()[0], smT = t.rv2.srcObject && t.rv2.srcObject.getVideoTracks()[0];
    var vid = !!(bigT && bigT.readyState === 'live' && !bigT.muted) && (L.gotState ? rs.cam || rs.screen : true);
    var small = !!(vid && smT && smT.readyState === 'live' && !smT.muted);
    t.box.classList.toggle('has-video', vid); t.box.classList.toggle('has-small', small);
    t.box.classList.toggle('peer-screen', vid && !!rs.screen);
    t.box.classList.toggle('muted', rs.mic === false);
    t.box.classList.toggle('wait', !L.connected);
    t.dv.textContent = [x.dev_name || '', x.role === 'companion' ? T('Begleitgerät') : ''].filter(Boolean).join(' · ');
    t.box.setAttribute('aria-label', (L.peer && L.peer.name || '?') + (x.dev_name ? ' (' + x.dev_name + ')' : '') + (rs.mic === false ? ' – ' + T('stumm') : ''));
    if (vid && t.rv.paused) t.rv.play().catch(function () {});
    if (small && t.rv2.paused) t.rv2.play().catch(function () {});
  }
  // Sprecher hervorheben: Pegel der empfangenen Tonspur (RTP-Quelle, ohne getStats) - 3x je Sekunde
  function speakers(R) {
    clearInterval(R.spkT);
    R.spkT = setInterval(function () {
      if (!live(R)) { clearInterval(R.spkT); return; }
      if (!meshView(R)) return;
      var best = null, top = 0.03, now = Date.now();
      links(R).forEach(function (L) {
        var rc = L.pc && L.pc.getReceivers ? L.pc.getReceivers().filter(function (r) { return r.track && r.track.kind === 'audio'; })[0] : null;
        var src = rc && rc.getSynchronizationSources ? rc.getSynchronizationSources()[0] : null;
        L.level = src && typeof src.audioLevel === 'number' && L.remoteState.mic !== false ? src.audioLevel : 0;
        if (L.level > top) { top = L.level; best = L; }
      });
      if (best) { R.spk = best.peerDevice; R.spkAt = now; } else if (now - (R.spkAt || 0) > 1500) R.spk = null;
      links(R).forEach(function (L) { if (L.tile) L.tile.box.classList.toggle('speaking', !!R.spk && R.spk === L.peerDevice); });
    }, 330);
  }

  // --- 2.4: Leiste "Anruf laeuft" (anderes eigenes Geraet im Anruf, Gruppenanruf im Chat) ---------------------------------
  var RUN = {}, runT = null;
  function runCheck() { clearTimeout(runT); runT = setTimeout(pending, 400); }
  function hideRun(id) { var b = RUN[id]; if (b) { b.remove(); delete RUN[id]; } }
  function joinHere(id, role, video) {
    if (CHATS && bridge()) { join(id, role, video); return; }
    if (CHATS) { HK.toast(T('Zum Annehmen erst Chats auf diesem Gerät einrichten bzw. entsperren.')); return; }
    var h = '/chats#join-' + id + '-' + role + (video ? '-video' : '');
    var w = window.open(h, 'hk-chats'); if (w) w.focus(); else location.href = h;
  }
  function runBar(x) {
    var here = bridge() ? myDev().id : null, mine = x.mine || [];
    var group = x.kind === 'group' && !x.dm;               // 2.5: 1:1 ueber Knoten wie 1:1
    var show = !(C && C.id === x.id) && x.state !== 'ended' && !RINGS[x.id] && (here === null || mine.indexOf(here) < 0)
      && (group ? true : x.state === 'active' && mine.length > 0);
    if (!show) { hideRun(x.id); return; }
    var b = RUN[x.id];
    if (!b) { b = RUN[x.id] = el('div', 'cl-run'); b.setAttribute('role', 'status'); document.body.appendChild(b); }
    b.textContent = '';
    var other = (x.legs || []).filter(function (l) { return mine.indexOf(l.device) >= 0; });
    var main = other.filter(function (l) { return l.role !== 'companion'; })[0] || other[0];
    var video = x.media === 'video';
    b.appendChild(el('span', 'cl-run-ic', video ? '🎥' : '📞'));
    var txt = el('span', 'cl-run-t');
    if (mine.length) txt.textContent = T('Anruf läuft auf {dev}', { dev: (main && main.dev_name) || T('anderem Gerät') }) + (group ? '' : ' · ' + ((x.peer && x.peer.name) || ''));
    else txt.textContent = T('Gruppenanruf läuft') + ' · ' + T('{n} Geräte', { n: (x.legs || []).length });
    b.appendChild(txt);
    if (!x.joinable) { b.appendChild(el('span', 'cl-run-s', blockText(x.join_block, x.limit))); return; }
    if (mine.length) {
      b.appendChild(btn(T('Hinzufügen'), 'cl-pill', function () { joinHere(x.id, 'companion', false); }, T('Dieses Gerät als Begleitgerät hinzufügen (ohne Mikrofon und Ton)')));
      b.appendChild(btn(T('Hierher übernehmen'), 'cl-pill cl-run-go', function () { joinHere(x.id, 'handover', video); }, T('Anruf ohne Unterbrechung auf dieses Gerät holen')));
    } else {
      if (video) b.appendChild(btn(T('Nur mit Ton'), 'cl-pill', function () { joinHere(x.id, 'member', false); }));
      b.appendChild(btn(T('Beitreten'), 'cl-pill cl-run-go', function () { joinHere(x.id, 'member', video); }, T('Dem Gruppenanruf beitreten')));
    }
  }

  // --- Verbindung, Pruefung, Qualitaet ---------------------------------------------------------------------------------
  function onConn(c) {
    if (!live(c) || !c.pc) return;
    var st = c.pc.connectionState || c.pc.iceConnectionState, ice = c.pc.iceConnectionState;
    if ((st === 'connected' || st === 'completed') && ice !== 'failed' && ice !== 'disconnected') {
      clearTimeout(c.failT); clearTimeout(c.discT); c.discT = null; c.failT = null; c.tries = 0;
      if (c.restarting) { c.restarting = false; c.path = ''; c.net = ''; }
      if (!c.connected) {
        c.connected = true; c.t0 = Date.now(); stopTone(); clearTimeout(c.connT); stateOut(c);
        var R = roomOf(c);
        if (!R.since) R.since = c.t0;
        screenOn(c);                                   // 2.4: teile ich schon, bekommt eine neue Verbindung den Bildschirm
        hoCheck(R); drawInfo(R);
      }
      status(c, ''); roomSettled(c); tick(c); poll(c);
    } else if (st === 'disconnected' || ice === 'disconnected') {
      status(c, T('Verbindung unterbrochen – versuche es weiter …'));
      // 2.4: kommt die Verbindung nicht von selbst wieder (Netzwechsel), ICE neu aushandeln
      if (!c.discT) c.discT = setTimeout(function () { c.discT = null; var x = c.pc && c.pc.iceConnectionState; if (live(c) && (x === 'disconnected' || x === 'failed')) restartIce(c); }, DISC_S * 1000);
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
      if (!live(c)) return;
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
    if (!c.pc || !c.pc.getSenders || !live(c)) return;
    var path = pathOf(c), want = c.remoteState.want || 'large', share = !!(c.screen && c.scrMode === 'track');
    c.pc.getSenders().forEach(function (snd) {
      var tr = snd.track; if (!tr || !snd.getParameters || !snd.setParameters) return;
      var kind = tr.kind === 'audio' ? 'audio' : tr === c.screen ? 'screen' : 'cam';
      // Platz je Sender: audio, video (Kamera-m-Zeile, in Betriebsart A auch der Bildschirm), screen (eigene m-Zeile, E3)
      var slot = tr.kind === 'audio' ? 'audio' : c.scrTr && snd === c.scrTr.sender ? 'screen' : 'video';
      var h = 0; try { h = (tr.getSettings && tr.getSettings().height) || 0; } catch (e) { h = 0; }
      var want_ = rules(kind, path, want, endpoints(c), h, share), key = kind + ':' + JSON.stringify(want_);    // 2.4: ab 3 Endpunkten Mesh-Werte
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
  // Wiedergabe-Elemente einer Verbindung: eigene Kachel (Kachelansicht) bzw. die grossen Elemente des Anruffensters
  function els(c) {
    if (c.tile) return c.tile;
    var R = roomOf(c);
    return R.ui && !meshView(R) && primary(R) === c ? R.ui : null;     // Einzelansicht: nur die Hauptverbindung
  }
  function bindRemote(c) {
    var E = els(c);
    if (!E) return;
    var live = function (t) { return t.readyState === 'live'; };
    var rvt = remoteVideo(c), big = [], small = [];
    if (rvt.screen) {
      var sc = rvt.screen, cm = rvt.camAny && c.remoteState.cam ? rvt.camAny : null;
      if (c.swap && cm) { big = [cm]; small = [sc]; } else { big = [sc]; small = cm ? [cm] : []; }
    } else if (rvt.camAny) big = [rvt.camAny];
    else big = c.remote.getVideoTracks().filter(live).slice(0, 1);
    [[E.ra, c.remote.getAudioTracks().filter(live)], [E.rv, big.filter(live)], [E.rv2, small.filter(live)]].forEach(function (x) {
      var m = x[0], want = x[1], have = m.srcObject ? m.srcObject.getTracks() : [];
      if (want.length === have.length && want.every(function (t) { return have.indexOf(t) >= 0; })) return;
      m.srcObject = want.length ? new MediaStream(want) : null;
    });
    playAudio(c);
  }
  // play() kann an der Autoplay-Regel scheitern (Anruf im neu geoeffneten Tab, Safari ohne Bedienung): dann Knopf
  // "Ton einschalten" - jede Bedienung im Fenster versucht es erneut
  function playAudio(c) {
    var E = els(c), ra = E && E.ra;
    // Begleitgeraet: Wiedergabe stumm (Echo-Schutz); Ton eigener Geraete nie abspielen
    if (ra) ra.muted = !roomOf(c).spkOut || !!(c.peer && c.peer.me);
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
  document.addEventListener('pointerdown', function () { if (C) each(C, function (L) { if (L.audioBlocked) playAudio(L); }); }, true);
  document.addEventListener('keydown', function () { if (C) each(C, function (L) { if (L.audioBlocked) playAudio(L); }); }, true);
  // Esc schliesst Runensiegel bzw. Teilnehmerliste im Anruf (Nr. 071)
  document.addEventListener('keydown', function (e) {
    var H = C && C.horn; if (e.key !== 'Escape' || !H || !(H.rpop || H.plist)) return;
    e.preventDefault(); e.stopPropagation();
    if (H.rpop) { H.rpop = false; hornRunePop(C); drawInfo(C); } else { H.plist = false; hornList(C); drawCtl(C); }
  });
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
        c.sinkId = sel.value === 'default' ? '' : sel.value;           // auch fuer die Kacheln (Kachelansicht)
        ra.setSinkId(c.sinkId).catch(function (e) { HK.toast(T('Tonausgabe nicht umschaltbar: {err}', { err: e.message })); });
        each(c, function (L) { if (L.tile && L.tile.ra.setSinkId) L.tile.ra.setSinkId(c.sinkId).catch(function () {}); });
      };
      sel.addEventListener('change', function () { try { localStorage.setItem('saga-call-sink', sel.value); } catch (e) { /* privat */ } apply(); });
      if (want && sel.value === want && want !== 'default') apply();
      c.ui.sink = sel; c.ui.info.parentNode.insertBefore(sel, c.ui.info.nextSibling);
    }).catch(function () {});
  }
  function tick(c) {
    var R = roomOf(c);
    clearInterval(R.statsT);
    R.statsT = setInterval(function () { if (!live(R)) return clearInterval(R.statsT); each(R, poll); }, 2000);
  }
  // 2.4: Lebenszeichen mit dem eigenen Geraet - "left": dieses Geraet ist nicht mehr im Anruf (z. B. uebernommen)
  function aliveOn(c) {
    c = roomOf(c);
    clearInterval(c.alive);
    c.alive = setInterval(function () {
      if (!live(c) || !c.id) return clearInterval(c.alive);
      api('POST', '/api/calls/' + c.id + '/alive', { device: myDev().id }).then(function (r) {
        if (r.state === 'ended') remoteEnded(c, { reason: r.reason });
        else if (r.leg === 'left' && !c.done) finish(c, T('Dieses Gerät ist nicht mehr im Anruf.'), 'info');
      }).catch(function () {});
    }, ((cfg && cfg.alive_s) || 15) * 1000);
  }

  // --- Bedienung -------------------------------------------------------------------------------------------------
  // Zustand ans Gegenueber. 2.4: dazu pip, want (wie gross es uns zeigen soll) und path - immer, auch ohne reneg
  // (aeltere Gegenstellen ignorieren die Felder)
  function wantNow(c) { return c.pip || document.visibilityState === 'hidden' ? 'small' : 'large'; }
  function stateAll(c) { var R = roomOf(c); if (R && R.horn) { hornStateOut(R); hornList(R); return; } each(c, stateOut); }
  function stateOut(c) {
    if (!c.ready || !c.pc && !c.peerDevice) return;
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
    visT = setTimeout(function () { var c = C; if (c && !c.done && links(c).some(function (L) { return L.connected; }) && wantNow(c) !== c.wantSent) stateAll(c); }, 600);
  });
  function toggleMic(c) { setMic(roomOf(c), !roomOf(c).mic); }
  // Mikrofon an/aus fuer alle Verbindungen. Begleitgeraet ohne Mikrofon-Spur: erst jetzt freigeben und einsetzen
  function setMic(c, on) {
    c = roomOf(c);
    var a = c.local ? c.local.getAudioTracks()[0] : null;
    if (on && !a && c.local && navigator.mediaDevices) {
      navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }).then(function (s) {
        if (!live(c)) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
        var t = s.getAudioTracks()[0]; c.local.addTrack(t);
        each(c, function (L) { var snd = L.pc && audioSender(L); if (snd && !c.mix) snd.replaceTrack(t).then(function () { delete L.rates.audio; applyRates(L); }); });
        setMic(c, true);
      }).catch(function (e) { HK.toast(mediaError(e, false)); });
      return;
    }
    c.mic = !!on;
    (c.local ? c.local.getAudioTracks() : []).forEach(function (t) { t.enabled = c.mic; });
    try { if (navigator.mediaSession && navigator.mediaSession.setMicrophoneActive) navigator.mediaSession.setMicrophoneActive(c.mic); } catch (e) { /* egal */ }
    drawCtl(c); stateAll(c);
  }
  // Wiedergabe an/aus (Begleitgeraet: aus, damit es im selben Raum nicht rueckkoppelt)
  function setSpk(c, on) {
    c = roomOf(c); c.spkOut = !!on;
    each(c, function (L) { var E = els(L); if (E && E.ra) E.ra.muted = !c.spkOut; playAudio(L); });
    if (c.ui) c.ui.ra.muted = !c.spkOut;
    hornSpk(c);
    drawCtl(c);
  }
  function toggleCam(c) {
    c = roomOf(c);
    if (c.cam) {
      c.cam.stop(); if (c.local) c.local.removeTrack(c.cam); c.cam = null;
      each(c, function (L) { var snd = L.pc && videoSender(L); if (snd && (!c.screen || L.scrMode === 'track')) snd.replaceTrack(null); });
      hornCam(c);
      drawMedia(c); drawCtl(c); stateAll(c); return;
    }
    navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' } }).then(function (s) {
      if (!live(c)) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
      c.cam = s.getVideoTracks()[0];
      if (!c.local) c.local = new MediaStream();
      c.local.addTrack(c.cam);
      each(c, function (L) {
        var snd = L.pc && videoSender(L);
        if (snd && (!c.screen || L.scrMode === 'track')) snd.replaceTrack(c.cam).then(function () { delete L.rates.video; applyRates(L); });
      });
      hornCam(c);
      drawMedia(c); drawCtl(c); stateAll(c);
    }).catch(function (e) { HK.toast(mediaError(e, true)); });
  }
  // --- 2.4 E3: Bildschirm teilen ------------------------------------------------------------------------------------
  // Betriebsart B ('track'): eigene Videospur ueber Neuverhandlung, die Kamera bleibt (gedrosselt). A ('replace', 2.1/2.3
  // und E0-Gegenstellen ohne screen2): replaceTrack auf dem Kamera-Sender wie bisher.
  function hintOff() { try { return localStorage.getItem('saga-screen-hint') === '0'; } catch (e) { return false; } }
  function toggleScreen(c) {
    c = roomOf(c);
    if (!c.horn && !links(c).some(function (L) { return L.pc && videoSender(L); })) return;
    if (c.horn && !c.horn.cur) return;
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
    var grpH = roomOf(c).kind === 'group' && !roomOf(c).dm;   // Gruppe: alle im Anruf sehen es
    box.appendChild(el('p', null, grpH ? T('Alles, was in der Auswahl sichtbar ist, sehen alle im Anruf – auch Benachrichtigungen, Passwörter und andere Fenster oder Chats.')
      : T('Alles, was in der Auswahl sichtbar ist, sieht dein Gegenüber – auch Benachrichtigungen, Passwörter und andere Fenster oder Chats.')));
    box.appendChild(el('p', 'cl-hint-s', T('Nichts wird aufgezeichnet oder auf dem Server gespeichert. Teile am besten nur ein Fenster oder einen Tab.')));
    var lab = el('label', 'cl-hint-c'), cb = el('input'); cb.type = 'checkbox'; lab.appendChild(cb); lab.appendChild(document.createTextNode(' ' + T('Nicht mehr anzeigen')));
    box.appendChild(lab);
    var row = el('div', 'cl-hint-b');
    var close = function () { box.remove(); if (c.ui) c.ui.hint = null; };
    row.appendChild(btn(T('Abbrechen'), 'cl-pill cl-hint-no', close));
    row.appendChild(btn(T('Bildschirm wählen'), 'cl-pill cl-hint-go', function () {
      if (cb.checked) { try { localStorage.setItem('saga-screen-hint', '0'); } catch (e) { /* privat */ } }
      close(); if (live(c) && !c.done) pickScreen(c);
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
      if (!live(c) || c.done || c.screen) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
      var v = s.getVideoTracks()[0]; if (!v) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
      c.screen = v; c.scrStream = s; c.shareStarts += 1;
      c.screen.onended = function () { if (live(c) && c.screen === v) stopScreen(c); };
      try { c.screen.contentHint = 'detail'; } catch (e) { /* aeltere Browser */ }
      var at = s.getAudioTracks()[0];
      if (at) mixOn(c, at);
      each(c, function (L) { screenOn(L); });            // 2.4: je Verbindung die Betriebsart nach ihren Faehigkeiten
      hornScreen(c);                                    // 2.5: ueber den Knoten (eigener Sender je Bild-Leitung)
      drawMedia(c); drawCtl(c); drawShare(c);
    }).catch(function (e) { if (e && e.name !== 'NotAllowedError' && e.name !== 'AbortError') HK.toast(T('Bildschirm teilen nicht möglich: {err}', { err: e.message })); });
  }
  // Bildschirm auf einer Verbindung einsetzen (auch fuer eine spaeter hinzukommende): B (eigene Spur) bzw. A (replaceTrack)
  function screenOn(L) {
    var R = roomOf(L);
    if (!R.screen || !L.pc || !videoSender(L) || L.scrMode) return;
    L.scrMode = canScreen2(L) && L.established ? 'track' : 'replace';
    if (L.scrMode === 'track') { screenTrackOn(L); stateLater(L); }
    else { videoSender(L).replaceTrack(R.screen).then(function () { delete L.rates.video; applyRates(L); }); stateOut(L); }
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
    c = roomOf(c);
    var v = c.screen;
    if (v) { v.onended = null; v.stop(); }
    if (c.scrStream) c.scrStream.getTracks().forEach(function (t) { t.stop(); });
    mixOff(c);
    c.screen = null; c.scrStream = null; c.camPaused = false;
    each(c, screenOff);
    hornScreen(c);
    drawMedia(c); drawCtl(c); drawShare(c);
  }
  function screenOff(c) {
    var mode = c.scrMode;
    c.scrMode = '';
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
    if (!c.stateDue) stateOut(c);
  }
  // Zustand nach der Neuverhandlung senden (screen_mid steht erst nach setLocalDescription fest); offerNow schickt ihn
  // direkt nach dem Angebot, sonst spaetestens nach 3 s
  function stateLater(c) {
    c.stateDue = true;
    setTimeout(function () { if (live(c) && c.stateDue) { c.stateDue = false; stateOut(c); } }, 3000);
  }
  // Systemton in die Tonspur mischen (Mikrofon + Ton der Quelle). Stumm schaltet weiter nur das Mikrofon.
  function mixOn(c, at) {
    c = roomOf(c);
    var ctx = audioCtx(), mic = c.local && c.local.getAudioTracks()[0];
    if (!ctx || !ctx.createMediaStreamDestination) return;
    try {
      var dst = ctx.createMediaStreamDestination(), nodes = [];
      if (mic) { var a = ctx.createMediaStreamSource(new MediaStream([mic])); a.connect(dst); nodes.push(a); }
      var b = ctx.createMediaStreamSource(new MediaStream([at])); b.connect(dst); nodes.push(b);
      var out = dst.stream.getAudioTracks()[0];
      c.mix = { dst: dst, nodes: nodes, out: out, src: at };
      each(c, function (L) { var snd = L.pc && audioSender(L); if (snd) snd.replaceTrack(out).then(function () { delete L.rates.audio; applyRates(L); }); });
    } catch (e) { c.mix = null; }
  }
  function mixOff(c) {
    c = roomOf(c);
    var m = c.mix; if (!m) return;
    c.mix = null;
    var mic = c.local && c.local.getAudioTracks()[0];
    each(c, function (L) {
      var snd = L.pc && audioSender(L);
      if (snd) snd.replaceTrack(mic || null).then(function () { delete L.rates.audio; applyRates(L); }).catch(function () {});
    });
    m.nodes.forEach(function (n) { try { n.disconnect(); } catch (e) { /* egal */ } });
    try { m.out.stop(); } catch (e) { /* egal */ }
  }
  function hangup(c) {
    c = roomOf(c);
    if (!live(c)) return;
    if (!c.ending) each(c, function (L) { if (L.established && peerCan(L, 'bye')) sendSignal(L, { t: 'bye', reason: 'hangup' }); });
    // 2.4: mit device - Begleitgeraet bzw. Gruppe: nur dieses Geraet verlaesst den Anruf; Hauptgeraet (1:1): Ende fuer alle
    if (c.id && !c.ending) api('POST', '/api/calls/' + c.id + '/end', { reason: 'hangup', connected: anyConn(c), device: myDev().id }).catch(function () {});
    finish(c, anyConn(c) ? T('Anruf beendet · {d}', { d: durNow(c) }) : T('Anruf beendet'));
  }
  function anyConn(c) { return !!roomOf(c).since; }
  // 2.5 (Testrunde Web Nr. 075/089): Dauer fuer alle gleich und passend zum Chat-Eintrag - ab dem Beginn des Gespraechs
  // laut Server (answered, umgerechnet mit seiner Uhr "now"), nicht ab dem eigenen Verbinden; immer aus Zeitstempeln
  function callT0(R, k) {
    if (!k || typeof k.answered !== 'number' || typeof k.now !== 'number') return;
    var t = k.answered * 1000 + (Date.now() - k.now * 1000);
    if (t <= Date.now()) R.t0s = t;
  }
  function sinceOf(R) {
    R = roomOf(R);
    if (R.since && R.t0s && R.t0s < R.since) R.since = R.t0s;   // erst ab dem eigenen Verbinden (since ist auch "verbunden")
    return R.since;
  }
  function durNow(R) { return dur((Date.now() - (sinceOf(R) || Date.now())) / 1000); }
  // Laufende Dauer jede Sekunde nachfuehren (die Zeile selbst zeichnet sonst nur der Statistik-Takt, unter Last seltener)
  setInterval(function () {
    var R = C; if (!R || R.done || !R.ui || !sinceOf(R)) return;
    var t = durNow(R), P = primary(R);
    [R.ui.info, P && P.ui && P.ui.info].forEach(function (i) {
      if (!i) return;
      [].forEach.call(i.children, function (x) { if (x.tagName === 'SPAN' && !x.children.length && /^(\d+:)?\d+:\d\d$/.test(x.textContent)) x.textContent = t; });
    });
  }, 1000);
  function fail(c, msg, reason) {
    if (!live(c)) return;
    if (c.room || c.established && !c.ending && (roomOf(c).kind === 'group' || links(c).length > 1)) {   // 2.4: nur diese Verbindung
      dropLink(c, msg || '');
      return;
    }
    if (c.id && !c.ending) api('POST', '/api/calls/' + c.id + '/end', { reason: reason === 'failed' ? 'failed' : 'hangup', connected: anyConn(c),
                                                                    device: bridge() ? myDev().id : undefined }).catch(function () {});
    finish(c, msg || T('Anruf beendet'), 'error');
  }
  function securityFail(c) {
    if (c && c.peer && c.peer.name && (c.room || links(c).length > 1)) HK.toast(T('{name}: Sicherheitsprüfung fehlgeschlagen – Verbindung getrennt.', { name: c.peer.name }));
    fail(c, T('Sicherheitsprüfung fehlgeschlagen: Die Signatur bzw. der Schlüssel der Gegenseite passt nicht. Der Anruf wurde abgebrochen – möglicherweise versucht jemand mitzuhören. Prüfe die Sicherheitsnummer im Chat.'), 'failed');
  }
  function remoteEnded(c, d) {
    c = roomOf(c);
    if (!live(c)) return;
    var r = d.reason, name = c.peer.name || '';
    var msg = r === 'declined' ? (c.role === 'caller' ? T('{name} hat abgelehnt.', { name: name }) : T('Abgelehnt.'))
      : r === 'busy' ? T('Besetzt – {name} telefoniert gerade.', { name: name })
      : r === 'timeout' ? (c.role === 'caller' ? T('{name} hat nicht abgenommen.', { name: name })
        : !c.ready ? T('Der Anruf ist abgelaufen, bevor Mikrofon/Kamera freigegeben waren – bitte zurückrufen.') : T('Verpasster Anruf.'))
      : r === 'cancelled' ? T('{name} hat aufgelegt.', { name: name })
      : r === 'lost' ? T('Verbindung verloren.')
      : r === 'failed' ? T('Die Verbindung kam nicht zustande.')
      : anyConn(c) ? T('Anruf beendet · {d}', { d: durNow(c) }) : T('Anruf beendet');
    c.ending = true;
    finish(c, msg, r === 'hangup' || !r ? '' : 'info');
  }
  function finish(c, msg, kind) {
    c = roomOf(c);
    if (c.done) return;
    c.done = true; c.ending = true;
    clearInterval(c.alive); clearInterval(c.statsT); clearInterval(c.spkT); clearTimeout(c.hoT);
    if (c.horn) hornStop(c);
    links(c).concat([c]).forEach(function (L) {
      clearTimeout(L.outT); clearTimeout(L.connT); clearTimeout(L.failT); clearTimeout(L.discT);
      if (L !== c) { try { if (L.pc) L.pc.close(); } catch (e) { /* schon zu */ } L.gone = true; if (L.tile) { L.tile.box.remove(); L.tile = null; } }
    });
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
      c.ui.w.classList.remove('mesh'); c.ui.grid.textContent = '';
      c.ui.ctl.textContent = '';
      c.ui.ctl.appendChild(btn(T('Schließen'), 'cl-pill', function () { closeWin(c); }));
      if (kind !== 'error') setTimeout(function () { closeWin(c); }, 3500);
    } else if (msg) HK.toast(msg);
    if (location.hash.indexOf('#call-') === 0) history.replaceState(null, '', location.pathname);
    if (window.HKChat && window.HKChat.callApi && c.chat_id) window.HKChat.callApi.refresh(c.chat_id);
    runCheck();                                        // 2.4: laeuft der Anruf auf anderen Geraeten weiter -> Leiste
  }

  // 2.5: eigenes Bild - in eine Ecke ziehen (rastet ein; Klassen self-c0..3 am Fenster) und per Klick/Enter zur kleinen
  // Blase verkleinern bzw. wieder vergroessern. Beides merkt sich der Browser fuer alle Anrufe (ohne Wahl: wie bisher).
  function selfPrefs() { try { return JSON.parse(localStorage.getItem('saga-call-self') || '{}') || {}; } catch (e) { return {}; } }
  function selfSave(p) { try { localStorage.setItem('saga-call-self', JSON.stringify(p)); } catch (e) { /* privat */ } }
  function selfApply(w, lv) {
    var p = selfPrefs();
    [0, 1, 2, 3].forEach(function (i) { w.classList.toggle('self-c' + i, p.corner === i); });
    w.classList.toggle('self-min', !!p.hidden);
    lv = lv || w.querySelector('.cl-local');          // beim Aufbau haengt das Bild noch nicht im Fenster (D05)
    if (lv) {
      var l = p.hidden ? T('Eigenes Bild zeigen') : T('Klicken: eigenes Bild verkleinern · Ziehen: verschieben');
      lv.title = l; lv.setAttribute('aria-label', l);        // Tooltip und Vorlesetext gleich (D06)
    }
  }
  function selfPip(w, lv) {
    lv.tabIndex = 0; lv.setAttribute('role', 'button');
    var start = null, moved = false;
    lv.addEventListener('pointerdown', function (e) {
      if (e.button !== 0) return;
      start = { x: e.clientX, y: e.clientY }; moved = false;
      try { lv.setPointerCapture(e.pointerId); } catch (x) { /* egal */ }
    });
    lv.addEventListener('pointermove', function (e) {
      if (!start) return;
      var dx = e.clientX - start.x, dy = e.clientY - start.y;
      if (!moved && Math.abs(dx) + Math.abs(dy) < 6) return;
      moved = true; lv.classList.add('dragging'); lv.style.translate = dx + 'px ' + dy + 'px';
    });
    function end(e) {
      if (!start) return;
      var was = moved; start = null; lv.classList.remove('dragging'); lv.style.translate = '';
      var p = selfPrefs();
      if (was) {
        var wr = w.getBoundingClientRect(), cx = e.clientX, cy = e.clientY;     // der Zeiger zaehlt
        p.corner = (cx < wr.left + wr.width / 2 ? 0 : 1) + (cy < wr.top + wr.height / 2 ? 0 : 2);
      } else p.hidden = !p.hidden;
      selfSave(p); selfApply(w, lv);
    }
    lv.addEventListener('pointerup', end);
    lv.addEventListener('pointercancel', function () { start = null; lv.classList.remove('dragging'); lv.style.translate = ''; });
    lv.addEventListener('keydown', function (e) {
      var p = selfPrefs();
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); p.hidden = !p.hidden; }
      else if (/^Arrow/.test(e.key)) {                     // Tastatur: Pfeile verschieben in die Ecke
        e.preventDefault(); var c = p.corner == null ? 3 : p.corner;
        if (e.key === 'ArrowLeft') c = c & 2; else if (e.key === 'ArrowRight') c = (c & 2) | 1;
        else if (e.key === 'ArrowUp') c = c & 1; else c = (c & 1) | 2;
        p.corner = c;
      } else return;
      selfSave(p); selfApply(w, lv);
    });
    selfApply(w, lv);
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
    // 2.4 E4/E5: Kachelansicht (Gruppe bzw. mehrere Verbindungen) - je Verbindung eine Kachel (layout)
    var grid = el('div', 'cl-grid'); grid.setAttribute('role', 'list'); grid.setAttribute('aria-label', T('Teilnehmende'));
    stage.appendChild(rv); stage.appendChild(ra); stage.appendChild(who); stage.appendChild(rv2); stage.appendChild(grid); stage.appendChild(lv);
    selfPip(w, lv);
    var top = el('div', 'cl-top'), info = el('div', 'cl-info'), st = el('div', 'cl-status'); st.setAttribute('role', 'status');
    var mini = btn(icon('shrink'), 'cl-mini cl-shrink', function () { w.classList.toggle('small'); }, T('Kleiner/größer'));
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
    c.ui = { w: w, rv: rv, rv2: rv2, ra: ra, lv: lv, who: who, info: info, st: st, ctl: ctl, un: un, pipB: pipB, share: share, hint: null, grid: grid };
    mediaSession(c);
    unlockAudio(c);                                    // laeuft im Klick "Anrufen"/"Annehmen"
    bindRemote(c);
    drawPeer(c); drawCtl(c); drawInfo(c);
  }
  function closeWin(c) { if (c.ui) { c.ui.w.remove(); c.ui = null; } }
  function drawPeer(c) {
    c = roomOf(c);
    if (!c.ui) return;
    var p = c.kind === 'group' && !c.dm ? { name: c.title || T('Gruppenanruf'), avatar: null } : c.peer || {}, who = c.ui.who; who.textContent = '';
    var av = HK.avatarImg(el('span', 'cl-av cl-av-big', (p.name || '?').charAt(0).toUpperCase()), p.avatar, 96); av.setAttribute('aria-hidden', 'true');
    who.appendChild(av); who.appendChild(el('div', 'cl-name', p.name || p.username || ''));
  }
  function status(c, s, err) {
    if (c && c.tile) { c.tile.st.textContent = s || ''; c.tile.st.hidden = !s; return; }   // Kachel: Zustand dieser Verbindung
    var R = roomOf(c);
    if (c !== R && meshView(R)) return;
    if (s && !err && c === R && meshView(R) && links(R).some(function (L) { return L.connected; })) return;   // W10: nicht wieder "Verbinde …"
    if (R.ui) { R.ui.st.textContent = s || ''; R.ui.st.classList.toggle('err', !!err); R.ui.st.hidden = !s; }
  }
  // 2.4 (Testrunde W10): Zeile des Anruffensters ("Klingelt bei den anderen …", "Verbinde …") weg, sobald zu irgendeinem
  // Geraet Medien fliessen - den Zustand je Verbindung zeigen dann die Kacheln (Gruppen-/Mehrgeraete-Ansicht).
  function roomSettled(c) {
    var R = roomOf(c);
    if (!R || !R.ui || R.done || !links(R).some(function (L) { return L.connected; })) return;
    R.ui.st.textContent = ''; R.ui.st.classList.remove('err'); R.ui.st.hidden = true;
  }
  // Hauptverbindung (Einzelansicht: deren Zustand zeigt das Anruffenster) - die erste sichtbare
  function primary(R) { R = roomOf(R); return shown(R)[0] || links(R)[0] || R; }
  function drawInfo(c) {
    var R = roomOf(c);
    if (!R.ui || R.done) return;
    if (R.horn) { hornInfo(R); return; }
    if (meshView(R)) { drawInfoMesh(R); return; }
    c = primary(R);
    var i = c.ui.info; i.textContent = '';
    if (R.companion) i.appendChild(el('span', 'cl-chip', T('Begleitgerät')));
    if (!c.ready && !c.connected) return;
    i.appendChild(lockEl(c.verified ? T('Ende-zu-Ende verschlüsselt') : T('verschlüsselt')));
    if (c.verified) i.lastChild.title = T('Medien mit DTLS-SRTP verschlüsselt; der Schlüssel-Fingerabdruck ist mit dem Chat-Schlüssel der Gegenseite signiert und geprüft.');
    if (c.connected) {
      i.appendChild(el('span', null, R.since ? durNow(R) : dur((Date.now() - c.t0) / 1000)));
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
  // Kachelansicht: Verschluesselung (alle Verbindungen geprueft), Dauer, Zahl der Teilnehmenden
  function drawInfoMesh(R) {
    var i = R.ui.info; i.textContent = '';
    if (R.companion) i.appendChild(el('span', 'cl-chip', T('Begleitgerät')));
    var ls = links(R), ver = ls.length && ls.every(function (L) { return L.verified; });
    i.appendChild(lockEl(ver ? T('Ende-zu-Ende verschlüsselt') : T('verschlüsselt')));
    if (ver) i.lastChild.title = T('Jede Verbindung ist mit DTLS-SRTP verschlüsselt; die Fingerabdrücke sind mit den Chat-Schlüsseln der Geräte signiert und geprüft.');
    if (R.since) i.appendChild(el('span', null, durNow(R)));
    i.appendChild(el('span', 'cl-count', T('{n} Geräte', { n: ls.length + 1 })));
    var blocked = ls.some(function (L) { return L.audioBlocked; });
    if (R.ui.un) R.ui.un.hidden = !blocked;
  }
  function drawMedia(c) {
    var R = roomOf(c);
    if (!R.ui || R.done) return;
    var mine0 = R.screen || R.cam;
    if (mine0) { if (!R.ui.lv.srcObject || R.ui.lv.srcObject.getTracks()[0] !== mine0) R.ui.lv.srcObject = new MediaStream([mine0]); }
    else R.ui.lv.srcObject = null;
    R.ui.lv.hidden = !mine0;
    R.ui.w.classList.toggle('screen', !!R.screen);
    if (c.tile) { drawTile(c); drawShare(R); playAudio(c); return; }
    if (R.horn) { hornDraw(R); return; }
    if (meshView(R)) { drawShare(R); pipBtn(R, false); return; }
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
    if (show && c.ui.rv.srcObject && c.ui.rv.paused) c.ui.rv.play().catch(function () {});
    pipBtn(c, show);
    pipDraw(c);
    drawShare(c);
    playAudio(c);
  }
  function swapView(c) { if (!live(c) || !c.remoteState.screen) return; c.swap = !c.swap; bindRemote(c); drawMedia(c); }
  // Leiste beim Teilen: eigene (rot, mit "Beenden") hat Vorrang; beim Empfaenger Name und - mit Kamera daneben - Tauschen
  function drawShare(c) {
    var R = roomOf(c);
    var b = R.ui && R.ui.share; if (!b || R.done) return;
    if (R.horn) return;                                // 2.5: hornShare (aus hornDraw)
    c = links(R).filter(function (L) { return L.remoteState.screen && L.connected; })[0] || primary(R);   // wer teilt (2.4: Mesh)
    var mesh = meshView(R);
    b.textContent = '';
    b.classList.toggle('me', !!c.screen);
    if (c.screen) {
      b.appendChild(el('span', 'cl-share-dot'));
      b.appendChild(el('span', 'cl-share-t', c.mix ? T('Du teilst deinen Bildschirm und den Ton') : T('Du teilst deinen Bildschirm')));
      if (c.camPaused) b.appendChild(el('span', 'cl-share-s', T('Kamera pausiert (langsame Verbindung)')));
      b.appendChild(btn(T('Teilen beenden'), 'cl-pill cl-share-stop', function () { if (live(c) && c.screen) stopScreen(c); }, T('Teilen beenden')));
      b.hidden = false;
    } else if (c.remoteState.screen && c.connected) {
      var name = (c.peer && (c.peer.name || c.peer.username)) || '';
      b.appendChild(el('span', 'cl-share-t', T('{name} teilt den Bildschirm', { name: name })));
      if (!mesh && c.ui.w.classList.contains('has-small')) b.appendChild(btn(icon('swap'), 'cl-mini cl-swap', function () { swapView(c); }, T('Ansicht tauschen')));
      b.hidden = false;
    } else b.hidden = true;
  }
  function drawCtl(c) {
    c = roomOf(c);
    if (!c.ui || c.done) return;
    var x = c.ui.ctl; x.textContent = '';
    var m = btn(icon(c.mic ? 'mic' : 'micOff'), 'cl-round' + (c.mic ? '' : ' off'), function () { toggleMic(c); }, c.mic ? T('Stummschalten') : T('Mikrofon einschalten'));
    m.setAttribute('aria-pressed', String(!c.mic)); x.appendChild(m);
    var v = btn(icon(c.cam ? 'cam' : 'camOff'), 'cl-round' + (c.cam ? '' : ' off'), function () { if (c.local) toggleCam(c); }, c.cam ? T('Kamera aus') : T('Kamera an'));
    v.setAttribute('aria-pressed', String(!c.cam)); x.appendChild(v);
    if (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia && !mobile) {
      var s = btn(icon('screen'), 'cl-round' + (c.screen ? ' on' : ''), function () { if (links(c).length || c.horn) toggleScreen(c); }, c.screen ? T('Teilen beenden') : T('Bildschirm teilen'));
      s.setAttribute('aria-pressed', String(!!c.screen)); x.appendChild(s);
    }
    // 2.4: Wiedergabe an/aus (Begleitgeraet startet stumm) und "Hierher uebernehmen" fuer ein Begleitgeraet
    if (c.companion || !c.spkOut || meshView(c)) {
      var k = btn(icon(c.spkOut ? 'spk' : 'spkOff'), 'cl-round cl-spk' + (c.spkOut ? '' : ' off'), function () { setSpk(c, !c.spkOut); },
                  c.spkOut ? T('Ton aus') : T('Ton an'));
      k.setAttribute('aria-pressed', String(!c.spkOut)); x.appendChild(k);
    }
    if (c.companion && c.ready) x.appendChild(btn(T('Hierher übernehmen'), 'cl-pill cl-take', function () { takeOver(c); }, T('Anruf auf dieses Gerät übernehmen')));
    if (c.horn && c.ready) {                           // 2.5: Ansicht (Kacheln/Sprecher) und Teilnehmerliste
      var H = c.horn, sv = H.view === 'speaker';
      var vb = btn(icon(sv ? 'grid' : 'spkView'), 'cl-round cl-view', function () { hornSetView(c, sv ? 'grid' : 'speaker'); }, sv ? T('Kachelansicht') : T('Sprecheransicht'));
      vb.setAttribute('aria-pressed', String(sv)); x.appendChild(vb);
      var pb = btn(icon('users'), 'cl-round cl-people' + (H.plist ? ' on' : ''), function () {
        H.plist = !H.plist;
        if (H.plist && H.rpop) { H.rpop = false; hornRunePop(c); drawInfo(c); }   // nie zwei Fenster uebereinander (Nr. 071)
        hornList(c); drawCtl(c);
      }, T('Teilnehmende'));
      pb.setAttribute('aria-pressed', String(!!H.plist)); x.appendChild(pb);
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
    c = roomOf(c);
    var pk = pipKind(), b = c.ui && c.ui.pipB; if (!b) return;
    b.hidden = !pk || (pk === 'video' && !show && !c.pip) || (meshView(c) && !c.horn);   // Mesh-Kacheln: kein Bild-im-Bild; Knoten: Sprecher
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
    if (!live(c) || c.done) { w.close(); return; }
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
    set('togglemicrophone', c ? function () { if (live(c)) toggleMic(c); } : null);
    set('hangup', c ? function () { if (live(c)) hangup(c); } : null);
    set('enterpictureinpicture', c ? function () { if (live(c) && !c.pip) pipOpen(c); } : null);
    try { if (c && ms.setMicrophoneActive) ms.setMicrophoneActive(c.mic); } catch (e) { /* egal */ }
  }

  // --- 2.5 Gjallarhorn (P3 Web-Minimum, P5 voller Web-Client): Gruppenanruf ueber Knoten ------------------------------
  // Der Server liefert Kandidaten (horn.candidates: Knoten mit je eigenen, 60 s gueltigen Medien-Token). Das Geraet misst
  // beim Beitritt alle parallel (TLS-Aufbau + 5 WSS-Pings ueber /probe, mit udp_ok dazu ein STUN-Binding; zusammen
  // hoechstens 800 ms), waehlt nach Punktzahl (static/horn.js, Konzept §2.6) und baut zum Knoten eine Ton- und eine
  // Bild-Leitung auf (H1; ICE-TCP, ohne Verbindung nach 4 s TURN-TLS) bzw. auf dem UDP-Weg eine Leitung mit BUNDLE. An jedem
  // Sender und Empfaenger haengt der Frame-E2E-Worker (static/frame-e2e.js, saga-horn/v1); den eigenen Frame-Schluessel
  // verteilt es als fkey ueber den versiegelten Signalweg (nie ueber den Knoten). Der Knoten sieht nur verschluesselte
  // Rahmen. Wer welche Plaetze belegt und welche CSRC jedes Geraet hat, schiebt der Knoten ueber den Ereigniskanal je Bein
  // (/api/sfu/leg/{id}/events) - beim Beitritt sofort, also vor dem ersten Rahmen. Peer-Kennung -> Geraet: Teilnehmerliste
  // (hpn je Knoten).
  // Live-Umschalten (make-before-break, Hysterese in static/horn.js): Verbindung weg (ICE disconnected > 2 s, Bein
  // beendet) bzw. Knoten leert sich -> sofort auf den naechsten Kandidaten; anderer Knoten deutlich besser -> nach den Regeln
  // in §2.6. Das neue Bein steht und sendet, bevor das alte schliesst; die Frame-Schluessel bleiben gleich (E2E haengt nicht
  // am Knoten). Ton: solange das neue Bein nichts liefert, spielt das alte (keine Luecke); Bild: ein Schluesselbild.
  // Kachelansicht bis 25 mit Last-N (Desktop 9 bewegt, Handy 4; die uebrigen als Standbild mit Sprechring),
  // Sprecheransicht, Bildschirm ueber den Knoten, Weg UDP/TCP/TLS mit Qualitaetspunkt, Runensiegel, Teilnehmerliste mit
  // Stummschalten und Entfernen (Gastgeber).
  // Last-N (H6): Desktop 9 bewegte Bilder, Handy 4 - der Sprecherplatz zaehlt mit (Kachelplaetze = Last-N - 1)
  var HORN_WAIT_TCP = 4000, HORN_WAIT_UDP = 2000, HORN_WAIT_TURN = 10000, HORN_TILES = (mobile ? 4 : 9) - 1, HORN_MON = 500, HORN_PING = 2000;
  var HORN_CANDS_EVERY = 30000, HORN_BAD_MS = 60000, HORN_RETRY_MAX = 120000;
  function hornCan() { return typeof RTCRtpScriptTransform === 'function' || !!(window.RTCRtpSender && RTCRtpSender.prototype.createEncodedStreams); }
  var HORN_V = (function () {
    var s = (document.currentScript && document.currentScript.src) || '', m = /[?&]v=([^&#]+)/.exec(s);
    return m ? '?v=' + m[1] : '';
  })();
  var HORN_SRC = '/static/frame-e2e.js' + HORN_V, HORN_LOGIC = '/static/horn.js' + HORN_V;
  var hornLibP = null;
  function hornScript(src, ready) {
    if (ready()) return Promise.resolve();
    return new Promise(function (ok, bad) {
      var s = document.createElement('script'); s.src = src;
      s.onload = function () { if (ready()) ok(); else bad(new Error('horn')); };
      s.onerror = function () { bad(new Error(T('Verschlüsselung für große Anrufe nicht ladbar.'))); };
      document.head.appendChild(s);
    });
  }
  function hornLib() {
    if (!hornLibP) hornLibP = Promise.all([hornScript(HORN_SRC, function () { return !!window.SagaFrameE2E; }),
                                           hornScript(HORN_LOGIC, function () { return !!window.SagaHorn; })])
      .then(function () { return window.SagaFrameE2E; }, function (e) { hornLibP = null; throw e; });
    return hornLibP;
  }
  function hornView() { try { return localStorage.getItem('saga-horn-view') === 'speaker' ? 'speaker' : 'grid'; } catch (e) { return 'grid'; } }
  function hornNew(R, blk) {
    R.horn = { blk: blk || {}, vown: {}, rseq: 0, X: null, L: null, key: null, q: [], peers: {}, known: {}, acks: {}, ackT: null, rekeyT: null, started: false,
               cur: null, next: null, olds: [], seq: 0, bad: {}, sw: null, meas: {}, switches: [], switching: false, retryT: null, retry0: 0,
               tiles: {}, more: null, view: R.dm ? 'speaker' : hornView(), st: {}, stT: null, monT: null, spkT: null, candT: 0, statT: 0, q5: null, net: null,
               runes: null, runesKey: '', plist: false, rpop: false, spk: null, speaking: {}, rosterT: 0, note: '', sent: 0 };
    return R.horn;
  }
  // 2.5 P6 (Robustheit): jede Anfrage an den Knoten mit Zeitlimit - ein Bein haengt nie stumm in "new"; Medien-Token
  // gelten 60 s - aeltere als 30 s vor dem Verbinden frisch holen (hornTry), nach 401 einmal neu (hornLeg)
  var HORN_FETCH_MS = 15000, HORN_FRESH_MS = 30000;
  function hornFetch(url, body, method) {
    var ac = window.AbortController ? new AbortController() : null, tm = ac ? setTimeout(function () { ac.abort(); }, HORN_FETCH_MS) : 0;
    return fetch(url, { method: method || (body ? 'POST' : 'GET'), mode: 'cors', credentials: 'omit', cache: 'no-store', signal: ac ? ac.signal : undefined,
                        headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        clearTimeout(tm);
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) { var e = new Error(j.error || ('HTTP ' + r.status)); e.status = r.status; throw e; }
          return j;
        });
      }, function (e) {
        clearTimeout(tm);
        throw new Error(T('Verbindung nicht aufgebaut: {err}', { err: e && e.name === 'AbortError' ? 'timeout' : ((e && e.message) || '?') }));
      });
  }
  // Gegenueber eines anderen Geraets fuer Versiegeln/Oeffnen (Schluessel je Person, im Raum zwischengespeichert)
  function hornPeer(R, x) {
    var H = R.horn, P = H.peers[x.device];
    if (!P) P = H.peers[x.device] = { id: R.id, v2: R.v2, room: R, peer: personOf(x), peerDevs: {}, sent: {} };
    return P;
  }
  // Ein POST /signal an mehrere Geraete: {geraet: body}
  function hornSend(R, bodies) {
    var devs = Object.keys(bodies), H = R.horn;
    if (!devs.length || !R.id || R.ending) return Promise.resolve();
    return Promise.all(devs.map(function (d) {
      var x = legOf(R, d); if (!x) return null;
      var P = hornPeer(R, x);
      return peerKeys(P).then(function () { return P.peerDevs[d] ? null : peerKeys(P, true); }).then(function () {
        return P.peerDevs[d] ? [d, seal(P, d, bodies[d])] : null;      // Geister-Geraet (nicht signiert): bekommt nichts
      }).catch(function () { return null; });
    })).then(function (list) {
      var sig = {}; list.forEach(function (x) { if (x) sig[x[0]] = x[1]; });
      if (!Object.keys(sig).length || !live(R)) return;
      return api('POST', '/api/calls/' + R.id + '/signal', { device: myDev().id, signals: sig }).catch(function (e) {
        if (e.status === 409) remoteEnded(R, { reason: 'gone' });
      });
    }).then(function () { if (H) H.sent = (H.sent || 0) + devs.length; });
  }
  function hornFkey(H, k) { return window.SagaFrameE2E.fkeyBody({ n: k.n, key: k.key, s: k.s }); }
  // Schluesselplan nach der Teilnehmerliste: Neue bekommen den (geratschten) neuesten Schluessel, nach einem Abgang ein
  // neuer Zufallsschluessel an alle Verbliebenen (Umschalten nach 1,5 s im Worker; Abgaenge binnen 0,5 s gebuendelt)
  function hornKeys(R) {
    var H = R.horn; if (!H || !H.key || !live(R)) return;
    var me_ = myDev().id, now = {}, add = [], gone = [];
    (R.legs || []).forEach(function (x) { if (x.device !== me_) now[x.device] = true; });
    Object.keys(now).forEach(function (d) { if (!H.known[d]) add.push(d); });
    Object.keys(H.known).forEach(function (d) { if (!now[d]) gone.push(d); });
    gone.forEach(function (d) { delete H.known[d]; delete H.st[d]; H.X.gone(d); });
    if (gone.length) {
      clearTimeout(H.rekeyT);
      H.rekeyT = setTimeout(function () {
        if (!live(R)) return;
        H.X.own('rekey').then(function (k) {
          H.key = k; var b = {}; Object.keys(H.known).forEach(function (d) { b[d] = hornFkey(H, k); });
          return hornSend(R, b);
        });
      }, 500);
    }
    if (!add.length) return;
    add.forEach(function (d) { H.known[d] = true; });
    var go = H.started ? H.X.own('ratchet') : Promise.resolve(H.key);   // erste Runde: Startschluessel an alle
    H.started = true;
    go.then(function (k) {
      H.key = k; var b = {};
      add.forEach(function (d) { b[d] = hornFkey(H, k); });
      return hornSend(R, b);
    }).then(function () { hornStateOut(R, add); });
  }
  // Signale im Knoten-Anruf: fkey (Schluessel eines Senders), fkey-ack (ok:false = Nachforderung), state (Mikrofon,
  // Kamera, Bildschirm - fuer Kacheln und Teilnehmerliste)
  function hornSig(R, d) {
    var H = R.horn;
    if (!H.X) { H.q.push(d); return; }
    var x = legOf(R, d.from_device);
    if (!x) { H.q.push(d); if (H.q.length > 200) H.q.shift(); return; }
    openSig(hornPeer(R, x), d).then(function (b) {
      if (!b || !live(R)) return;
      var F = window.SagaFrameE2E;
      if (b.t === 'fkey') {
        var k = F.parseFkey(b); if (!k) return;
        H.X.peer(d.from_device, k.n, k.key, k.s).then(function (okk) { if (okk) hornAck(R, d.from_device, k.n, k.s, true); });
      } else if (b.t === 'fkey-ack') {
        var a = F.parseFkeyAck(b);
        if (a && !a.ok) H.X.ownKeys().then(function (ks) { if (ks[0]) { var o = {}; o[d.from_device] = hornFkey(H, ks[0]); hornSend(R, o); } });
      } else if (b.t === 'state') {
        // "Hierher uebernehmen" im Knoten-Anruf: das neue eigene Geraet meldet ready + replaces = dieses Geraet
        if (b.ready && b.replaces === myDev().id && x.me) { handoverOut(R, d.from_device); return; }
        H.st[d.from_device] = { mic: b.mic !== false, cam: !!b.cam, screen: !!b.screen };
        if (Array.isArray(b.hp)) {                     // P9/P10: Kennungen des Absenders an seinen Knoten
          if (!H.hpx) H.hpx = {};
          H.hpx[d.from_device] = b.hp.filter(function (x) { return typeof x === 'string' && x.length <= 64; }).slice(0, 4);
          hornMap(R);
        }
        hornDraw(R); hornList(R);
      }
    });
  }
  function hornAck(R, to, n, s, okk) {
    var H = R.horn; if (!H) return;
    H.acks[to] = window.SagaFrameE2E.fkeyAckBody(n, s, okk);
    if (!H.ackT) H.ackT = setTimeout(function () { H.ackT = null; var b = H.acks; H.acks = {}; hornSend(R, b); }, 100);   // gebuendelt
  }
  // Eigener Zustand an alle (bzw. nur an neue Geraete) - gebuendelt
  function hornStateOut(R, only) {
    var H = R.horn; if (!H || !H.X || !live(R)) return;
    var send = function (devs) {
      var body = JSON.stringify({ t: 'state', mic: !!R.mic && !R.companion, cam: !!(R.cam && R.cam.readyState === 'live'), screen: !!R.screen,
                                  hp: hornMyRefs(R) }), b = {};
      devs.forEach(function (d) { if (H.known[d]) b[d] = body; });
      return hornSend(R, b);
    };
    if (only) { send(only); return; }
    clearTimeout(H.stT);
    H.stT = setTimeout(function () { H.stT = null; if (live(R)) send(Object.keys(H.known)); }, 300);
  }
  function hornDevName(R, dev) { var x = legOf(R, dev); return x ? (x.name || '?') : '?'; }
  function hornNote(R, s) { var H = R.horn; if (H) H.note = s || ''; if (s) status(R, s); }

  // --- Messung (§2.6): TLS-Aufbau + 5 WSS-Pings, dazu UDP (STUN-Binding) bei udp_ok; alles parallel, <= 800 ms ----------
  function hornProbe(c, ms) {
    var t0 = performance.now(), out = { ok: false, tls: null, rtts: [], udp: null };
    var wss = new Promise(function (done) {
      var ws, sent = {}, n = 0, fin = function () { try { ws.close(); } catch (e) { /* zu */ } done(); };
      try { ws = new WebSocket(c.url.replace(/^http/, 'ws') + '/probe'); } catch (e) { done(); return; }
      var ping = function () { n++; sent[n] = performance.now(); ws.send('p' + n); };
      ws.onopen = function () { out.tls = Math.round(performance.now() - t0); ping(); };
      ws.onmessage = function (e) {
        var k = +String(e.data).slice(1); if (!sent[k]) return;
        out.rtts.push(Math.round((performance.now() - sent[k]) * 10) / 10); out.ok = true;
        if (out.rtts.length >= window.SagaHorn.K.PINGS) fin(); else ping();
      };
      ws.onerror = fin; ws.onclose = function () { done(); };
      setTimeout(fin, ms);
    });
    var udp = !c.udp ? Promise.resolve() : new Promise(function (done) {
      var pc, u = new URL(c.url), t1 = performance.now(), fin = function () { try { pc.close(); } catch (e) { /* zu */ } done(); };
      try { pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:' + u.hostname + ':' + (u.port || 443) }] }); } catch (e) { done(); return; }
      // Antwort des Knotens auf das STUN-Binding: srflx-Kandidat; ohne NAT verwirft der Browser den (gleich der eigenen
      // Adresse) - dann zaehlt das Ende der Kandidatensuche ohne Fehler fuer diesen Server
      var err = false;
      out.ulog = [];
      pc.onicecandidate = function (e) {
        out.ulog.push(e.candidate ? (/ typ (\w+)/.exec(e.candidate.candidate) || [])[1] || '?' : 'ende');
        if (e.candidate && / typ srflx /.test(e.candidate.candidate)) { out.udp = Math.round(performance.now() - t1); fin(); }
      };
      pc.onicecandidateerror = function (e) { err = true; out.ulog.push('fehler ' + (e && e.errorCode)); };
      // nach der Frist: nur eigene Kandidaten, keine Antwort und kein Fehler = unklar (ohne NAT verwirft der Browser den
      // srflx-Kandidaten, die Suche auf anderen Schnittstellen laeuft noch) - dann probiert hornOpen UDP kurz aus
      setTimeout(function () { if (out.udp == null && !err && out.ulog.indexOf('host') >= 0) out.udpMaybe = true; }, ms - 5);
      pc.onicegatheringstatechange = function () { if (pc.iceGatheringState === 'complete' && !err && out.udp == null) { out.udp = Math.round(performance.now() - t1); fin(); } };
      pc.createDataChannel('p');
      pc.createOffer().then(function (o) { t1 = performance.now(); return pc.setLocalDescription(o); }).catch(fin);
      setTimeout(fin, ms);
    });
    return Promise.all([wss, udp]).then(function () { return out; });
  }
  function hornMeasure(R, cands) {
    var H = R.horn, ms = window.SagaHorn.K.PROBE_MS, m = {};
    return Promise.all(cands.map(function (c) { return hornProbe(c, ms).then(function (r) { m[c.node] = r; H.meas[c.node] = r; }); }))
      .then(function () { return m; });
  }
  function hornBad(H, node) { return (H.bad[node] || 0) > Date.now(); }
  // Gemiedene Knoten: nach Fehlern 60 s, dazu der Knoten, den das Geraet gerade wegen Leeren/Abbruch verlaesst (Saga erfaehrt
  // das Leeren erst mit der naechsten Lastmeldung)
  function hornAvoid(H, extra) {
    var a = Object.keys(H.bad).filter(function (n) { return hornBad(H, n); });
    if (H.leaving && H.leaving.until > Date.now() && a.indexOf(H.leaving.node) < 0) a.push(H.leaving.node);
    (extra || []).forEach(function (n) { if (a.indexOf(n) < 0) a.push(n); });
    return a;
  }
  function hornLog(H, conn, what) {
    (H.log = H.log || []).push([Date.now() % 100000, conn ? conn.node : '', what]);
    if (H.log.length > 30) H.log.shift();
  }
  function hornErr(H, node, e) {
    hornLog(H, null, 'err ' + node + ' ' + ((e && (e.message || String(e))) || '?'));
    (H.errs = H.errs || []).push({ t: Date.now(), node: node, err: (e && (e.message || String(e))) || '?', status: e && e.status });
    if (H.errs.length > 12) H.errs.shift();
  }
  function hornNoNode() { var e = new Error(T('Gerade ist kein Knoten für große Gruppenanrufe erreichbar – bitte gleich noch einmal.')); e.noNode = true; return e; }
  // Frische Kandidaten vom Server (avoid: diese Knoten nicht), node: dort den Endpunkt eintragen
  function hornCands(R, node, avoid) {
    var b = { device: myDev().id, dev_class: mobile ? 'mobile' : 'desktop', avoid: avoid || [] };
    if (node) b.node = node;
    return api('POST', '/api/calls/' + R.id + '/horn', b).then(function (r) {
      if (R.horn) R.horn.fedErr = (r.horn && r.horn.error) || '';   // P10: warum kein Knoten (anderer Server)
      if (R.horn) R.horn.fedWhy = (r.horn && r.horn.error && r.horn.fed && r.horn.fed.why) || '';   // Nr. 119: Grund fuer hornRetry
      return (r.horn && r.horn.candidates) || [];
    });
  }
  function hornFresh(R, cand, avoid) {
    return hornCands(R, cand.node, avoid).then(function (l) {
      var c = l.filter(function (x) { return x.node === cand.node; })[0];
      if (!c) throw hornNoNode();
      return c;
    });
  }

  // --- Verbindung zu einem Knoten: Beine, Ereigniskanal ------------------------------------------------------------------
  function hornGo(R, blk) {
    var H = R.horn || hornNew(R, blk);
    if (blk) { H.blk = blk; H.fedErr = blk.error || ''; }   // P10: kein Knoten fuer Geraete dieses Servers (Grund)
    if (!R.cam && R.local) { R.cam = R.local.getVideoTracks()[0] || null; drawCtl(R); drawMedia(R); }   // eigene Kamera (wie addLocal)
    (R.local ? R.local.getAudioTracks() : []).forEach(function (t) { t.enabled = R.mic; });
    status(R, T('Verbinde …'));
    return hornLib().then(function (F) {
      if (!live(R)) throw null;
      H.L = window.SagaHorn;
      H.X = F.create({ url: HORN_SRC });
      H.X.on('need', function (d) { hornAck(R, d.from, d.n, d.s, false); });
      H.X.on('vown', function (d) { H.vown[d.rid] = d.from; if (live(R)) hornDrawSoon(R); });
      return H.X.own('start');
    }).then(function (k) {
      H.key = k;
      hornKeys(R);                                     // Schluessel an alle (parallel zum Verbindungsaufbau)
      H.q.splice(0).forEach(function (d) { hornSig(R, d); });
      return hornStart(R, (H.blk && H.blk.candidates) || [], 'join');
    }).catch(function (e) {
      if (!live(R) || e === null) return;
      if (e && e.noNode) { hornRetry(R, 'join'); return; }
      fail(R, (e && e.message) || T('Verbindung nicht aufgebaut: {err}', { err: '?' }));
    });
  }
  // Kandidaten messen, nach Punktzahl ordnen, der Reihe nach versuchen. why: join | lost | draining | better | retry
  function hornStart(R, cands, why) {
    var H = R.horn;
    cands = cands.filter(function (c) { return !hornBad(H, c.node) || cands.length === 1; });
    if (!cands.length) return Promise.reject(hornNoNode());
    var one = cands.length === 1;
    // einziger Kandidat ohne UDP: gleich verbinden, Messung nur fuer die Anzeige; mit UDP erst messen (UDP-Probe, <= 800 ms)
    var m = one && !cands[0].udp ? Promise.resolve(null) : hornMeasure(R, cands);
    if (one && !cands[0].udp) hornMeasure(R, cands);
    return m.then(function (mm) {
      if (!live(R)) throw null;
      var list = one ? cands : H.L.rank(cands, mm, true).map(function (x) { return x.cand; });
      H.ranked = list.map(function (c) { return { node: c.node, score: one ? null : H.L.score(mm[c.node], c, true) }; });
      return hornTry(R, list, why, 0);
    });
  }
  function hornTry(R, list, why, i) {
    var H = R.horn, c = list[i];
    if (!c) return Promise.reject(hornNoNode());
    var first = (((H.blk && H.blk.candidates) || [])[0] || {}).node;
    // Join mit dem vom Server vorgemerkten Knoten: Token behalten, solange sie juenger als 30 s sind (gelten 60 s);
    // sonst Knoten melden und frische Token holen
    var young = c.exp && c.exp * 1000 - Date.now() > HORN_FRESH_MS;
    var fresh = why === 'join' && i === 0 && c.node === first && !c.used && young ? Promise.resolve(c) : hornFresh(R, c, hornAvoid(H));
    return fresh.then(function (c2) { c2.used = true; return hornOpen(R, c2); }).then(function (conn) {
      hornAdopt(R, conn, why);
      return conn;
    }, function (e) {
      if (!live(R) || e === null) throw null;
      hornErr(H, c.node, e);
      if (e && e.status === 404) throw new Error(T('Der Anruf ist schon beendet.'));
      H.bad[c.node] = Date.now() + HORN_BAD_MS;
      var nx = list[i + 1];
      if (nx) hornNote(R, T('Knoten {node} ist nicht erreichbar – versuche {next} …', { node: c.node, next: nx.node }));
      return hornTry(R, list, why, i + 1);
    });
  }
  // Weg waehlen: UDP (wenn der Knoten udp_ok meldet und die UDP-Probe klappt), sonst ICE-TCP, dann TURN-TLS
  function hornOpen(R, cand) {
    var H = R.horn, m = H.meas[cand.node];
    var udp = !!(cand.udp && cand.tokens && cand.tokens.both && m && (m.udp != null || m.udpMaybe));
    var p = udp ? hornConnect(R, cand, 'udp', m.udp != null ? HORN_WAIT_UDP : 1500).catch(function (e) { if (e === null) throw null; return hornConnect(R, cand, 'tcp'); })
      : hornConnect(R, cand, 'tcp');
    return p.catch(function (e) {
      if (e === null || (e && (e.status === 404 || e.status === 429))) throw e;      // beendet bzw. voll/Leeren: naechster Kandidat
      return hornFresh(R, cand, hornAvoid(H)).then(function (c2) { return hornConnect(R, c2, 'turn'); });
    });
  }
  function hornConnect(R, cand, path, waitMs) {
    var H = R.horn;
    var conn = { key: 'c' + (++H.seq), cand: cand, node: cand.node, url: cand.url, path: path, legs: {}, peers: {}, csrc: {}, csrcKey: '',
                 created: Date.now(), up: 0, rx: 0, dead: false, audios: [], rtts: [], pingN: 0, pings: {}, state: 'ok', ws: null };
    H.next = conn;
    var turnP = path === 'turn' ? hornFetch(cand.url + '/api/sfu/turn', { token: cand.tokens.audio }) : Promise.resolve(null);
    var roles = path === 'udp' ? ['both'] : ['audio', 'video'];
    return turnP.then(function (turn) {
      return Promise.all(roles.map(function (role) { return hornLeg(R, conn, role, path, turn); }));
    }).then(function () {
      return new Promise(function (ok, bad) {
        var wait = waitMs || (path === 'udp' ? HORN_WAIT_UDP : path === 'tcp' ? HORN_WAIT_TCP : HORN_WAIT_TURN);
        var t = setTimeout(function () { conn.onUp = null; var e = new Error('timeout'); e.timeout = true; bad(e); }, wait);
        conn.onUp = function (good) { clearTimeout(t); conn.onUp = null; if (good) ok(conn); else { var e = new Error('failed'); bad(e); } };
        hornConnState(R, conn);
      });
    }).then(function (c) { if (!live(R)) { hornConnClose(R, conn); throw null; } return c; }, function (e) {
      hornConnClose(R, conn);
      if (H.next === conn) H.next = null;
      throw live(R) ? e : null;
    });
  }
  function hornAudioTrack(R) { return R.mix ? R.mix.out : (R.local ? R.local.getAudioTracks()[0] : null) || null; }
  function hornLeg(R, conn, role, path, turn) {
    var H = R.horn, cand = conn.cand, cf = { bundlePolicy: 'max-bundle', rtcpMuxPolicy: 'require' };
    if (H.X.mode === 'streams') cf.encodedInsertableStreams = true;
    if (turn) {
      var u = new URL(cand.url);
      cf.iceServers = [{ urls: 'turns:' + u.hostname + ':' + (u.port || 443) + '?transport=tcp', username: turn.username, credential: turn.credential }];
      cf.iceTransportPolicy = 'relay';
    }
    var pc = new RTCPeerConnection(cf), st = R.local || new MediaStream(), L = { role: role, pc: pc, id: null, recv: [], aud: [], vid: [], slots: [], est: 0 }, i, t;
    conn.legs[role] = L;
    var recv = function (kind) {
      var x = pc.addTransceiver(kind, { direction: 'recvonly' }); x.rid = ++H.rseq; H.X.receiver(x.receiver, kind, '*', conn.key, x.rid);
      if (kind === 'video' && path !== 'udp') { try { if ('jitterBufferTarget' in x.receiver) x.receiver.jitterBufferTarget = 150; } catch (e) { /* aelterer Browser */ } }
      L.recv.push(x); (kind === 'audio' ? L.aud : L.vid).push(x);
      return x;
    };
    if (role !== 'video') {                             // Ton-Leitung bzw. UDP-Weg: eigener Ton + 3 Tonplaetze
      L.tr = pc.addTransceiver(hornAudioTrack(R) || 'audio', { direction: 'sendonly', streams: [st] });
      H.X.sender(L.tr.sender, 'audio');                // vor der Verhandlung: kein Rahmen geht unverschluesselt raus
      for (i = 0; i < 3; i++) recv('audio');
    }
    if (role !== 'audio') {
      // Simulcast (NODE.md): TCP-Wege l = 1/4, 20 fps L1T2 (Kacheln, der Knoten nimmt 10 fps), m = voll, 30 fps L1T3;
      // UDP zusaetzlich h (m dann halbe Groesse)
      var enc = path === 'udp' ? [
        { rid: 'l', scaleResolutionDownBy: 4, maxBitrate: 150000, maxFramerate: 20, scalabilityMode: 'L1T2' },
        { rid: 'm', scaleResolutionDownBy: 2, maxBitrate: 500000, maxFramerate: 30, scalabilityMode: 'L1T3' },
        { rid: 'h', scaleResolutionDownBy: 1, maxBitrate: 1500000, maxFramerate: 30, scalabilityMode: 'L1T3' }] : [
        { rid: 'l', scaleResolutionDownBy: 4, maxBitrate: 150000, maxFramerate: 20, scalabilityMode: 'L1T2' },
        { rid: 'm', scaleResolutionDownBy: 1, maxBitrate: 700000, maxFramerate: 30, scalabilityMode: 'L1T3' }];
      L.cam = pc.addTransceiver(R.cam || 'video', { direction: 'sendonly', streams: [st], sendEncodings: enc });
      H.X.sender(L.cam.sender, 'video');
      // Bildschirm: eigener Sender (mid an den Knoten: screen_mid), Raten wie 2.4 relay-tcp bzw. relay-udp
      var sr = RATES[path === 'udp' ? 'relay-udp' : 'relay-tcp'];
      L.scr = pc.addTransceiver(R.screen || 'video', { direction: 'sendonly', sendEncodings: [{ maxBitrate: sr.screen * 1000, maxFramerate: sr.screenFps }] });
      H.X.sender(L.scr.sender, 'video');
      recv('video');                                   // Sprecher
      recv('video');                                   // Bildschirm
      for (i = 0; i < HORN_TILES; i++) recv('video');  // Kacheln (Last-N)
    }
    pc.onconnectionstatechange = function () { hornConnState(R, conn); };
    var post = function (c) {
      return hornFetch(c.url + '/api/sfu/leg', { token: c.tokens[role], path: path, offer: pc.localDescription.sdp, audio: role !== 'video' ? 3 : 0,
                                                speaker: role !== 'audio', screen: role !== 'audio', tiles: role !== 'audio' ? HORN_TILES : 0,
                                                device: mobile ? 'mobile' : 'desktop', screen_mid: L.scr ? String(L.scr.mid) : '' });
    };
    return pc.createOffer().then(function (o) { return pc.setLocalDescription(o); }).then(function () {
      return post(cand).catch(function (e) {             // 401 (Token abgelaufen/benutzt): einmal mit frischem Token
        if (!e || e.status !== 401 || !live(R)) throw e;
        return hornFresh(R, cand, hornAvoid(H)).then(post);
      });
    }).then(function (res) {
      if (!live(R) || conn.legs[role] !== L || conn.dead) { try { pc.close(); } catch (e) { /* zu */ } throw null; }
      L.id = res.id; conn.mine = res.csrc; L.slots = res.slots || []; conn.state = res.state || 'ok';
      hornPeersIn(R, conn, res.peers || {});
      return pc.setRemoteDescription({ type: 'answer', sdp: res.answer });
    }).then(function () {
      hornEvents(R, conn, L);
      hornAudioEls(R, conn); hornMap(R); hornDraw(R);
    });
  }
  // Ereigniskanal je Bein: Teilnehmende/CSRC (sofort beim Beitritt), Belegung, Zustand des Knotens, Bein beendet, Ping
  function hornEvents(R, conn, L, again) {
    if (!window.WebSocket || !L.id || conn.dead) return;
    var ws, opened = false;
    try { ws = new WebSocket(conn.url.replace(/^http/, 'ws') + '/api/sfu/leg/' + encodeURIComponent(L.id) + '/events'); } catch (e) { return; }
    L.ws = ws;
    ws.onopen = function () { opened = true; };
    if (!conn.ws) conn.ws = ws;                        // Ping (RTT) ueber den ersten Kanal
    ws.onmessage = function (e) {
      var m; try { m = JSON.parse(e.data); } catch (x) { return; }
      if (!live(R) || conn.dead) return;
      if (m.t === 'hello' || m.t === 'peers') hornPeersIn(R, conn, m.peers || {});
      if (m.t === 'hello' || m.t === 'layout') { if (m.slots) L.slots = m.slots; L.est = m.est_kbit || L.est; hornMap(R); hornDraw(R); }
      if (m.t !== 'pong' && m.t !== 'layout') hornLog(R.horn, conn, m.t + (m.state ? ':' + m.state : '') + (m.why ? ':' + m.why : ''));
      if ((m.t === 'hello' || m.t === 'state') && m.state) {
        conn.state = m.state;
        if (m.state === 'draining' && R.horn.cur === conn) hornSwitch(R, 'draining');
      }
      if (m.t === 'closed') { conn.closed = m.why || 'zu'; if (R.horn.cur === conn) hornGone(R, conn); }
      if (m.t === 'pong' && conn.pings[m.n]) {
        conn.rtts.push(Math.round(performance.now() - conn.pings[m.n])); delete conn.pings[m.n];
        if (conn.rtts.length > 10) conn.rtts.shift();
      }
    };
    ws.onclose = function () {
      if (L.ws === ws) L.ws = null;
      if (conn.ws === ws) conn.ws = null;
      if (!live(R) || conn.dead || conn.closed) return;
      // Neuverbindung scheitert sofort (Knoten nimmt keine Verbindung mehr an): Knoten weg - nicht erst auf ICE warten
      if (again && !opened) { if (R.horn.cur === conn) { hornLog(R.horn, conn, 'events weg'); hornSwitch(R, 'lost'); } return; }
      // Kanal weg, Bein noch da (Netzwechsel): neu verbinden; sonst ueberwacht hornMon die PeerConnection
      setTimeout(function () { if (live(R) && !conn.dead && !L.ws) hornEvents(R, conn, L, true); }, 300);
    };
  }
  // Bein vom Knoten beendet: entfernt (Gastgeber) -> der Server meldet es; sonst wie Verbindung weg
  function hornGone(R, conn) {
    if (conn.closed === 'entfernt') {
      api('GET', '/api/calls/' + R.id + '?device=' + q(myDev().id)).then(function (r) {
        if (!live(R)) return;
        if (r.call.state === 'ended') remoteEnded(R, { reason: r.call.reason });
        else if (!(r.call.mine || []).length || (r.call.mine || []).indexOf(myDev().id) < 0) hornRemoved(R);
        else hornSwitch(R, 'lost');
      }).catch(function () { hornSwitch(R, 'lost'); });
      return;
    }
    hornSwitch(R, 'lost');
  }
  function hornPeersIn(R, conn, peers) {
    conn.peers = peers || {};
    hornMap(R);
    // unbekannte Kennung (Geraet gerade beigetreten bzw. Knoten neu bereit): Teilnehmerliste einmal neu holen
    var H = R.horn, me_ = myDev().id, unknown = Object.keys(conn.peers).some(function (ref) {
      var d = hornDevOf(R, conn, ref); return !d && ref !== hornMyRef(R, conn);
    });
    if (unknown && Date.now() - H.rosterT > 2000) {
      H.rosterT = Date.now();
      api('GET', '/api/calls/' + R.id + '?device=' + q(me_)).then(function (r) { if (live(R)) rosterIn(R, r.call.legs, r.call); }).catch(function () {});
    }
  }
  function hornRef(x, conn) { return x.hpn ? x.hpn[conn.node] : x.hp; }
  function hornMyRef(R, conn) { var x = myLeg(R); return (x && hornRef(x, conn)) || (conn.cand && conn.cand.peer) || ''; }
  // Kennung am Knoten -> Geraet: aus der Teilnehmerliste (Kennung je Knoten, hpn), P9/P10 auch Teilnehmende anderer Knoten
  // (Kaskade: sie behalten die Kennung ihres Knotens) - eigene Knoten ueber hpn, Knoten anderer Server ueber die Kennungen,
  // die jedes Geraet im versiegelten Zustand (state.hp) selbst meldet
  function hornDevOf(R, conn, ref) {
    var o = null, H = R.horn;
    if (!ref) return null;
    (R.legs || []).forEach(function (x) {
      if (hornRef(x, conn) === ref) o = x.device;
      else if (!o && x.hpn) Object.keys(x.hpn).forEach(function (k) { if (x.hpn[k] === ref) o = x.device; });
    });
    if (!o && H && H.hpx) Object.keys(H.hpx).forEach(function (d) { if (H.hpx[d].indexOf(ref) >= 0 && legOf(R, d)) o = d; });
    return o;
  }
  // Eigene Kennungen an den Knoten, mit denen das Geraet gerade verbunden ist (state.hp, hoechstens 4)
  function hornMyRefs(R) {
    var out = [];
    hornConns(R).forEach(function (c) { var r = hornMyRef(R, c); if (r && out.indexOf(r) < 0) out.push(r); });
    return out.slice(0, 4);
  }
  function hornConns(R) { var H = R.horn; return [H.cur].concat(H.olds, [H.next]).filter(function (c) { return c && !c.dead; }); }
  // CSRC -> Geraet je Knoten-Verbindung (CSRCs vergibt jeder Knoten selbst): aus peers (Ereigniskanal, POST /leg) und
  // aus der Belegung der Plaetze; nur Geraete aus der Teilnehmerliste
  function hornMap(R) {
    var H = R.horn; if (!H || !H.X) return;
    var me_ = myDev().id;
    hornConns(R).forEach(function (conn) {
      var cp = {}, m = {};
      Object.keys(conn.peers || {}).forEach(function (ref) { cp[conn.peers[ref] >>> 0] = ref; });
      Object.keys(conn.legs).forEach(function (r) { (conn.legs[r].slots || []).forEach(function (s) { if (s.peer && s.csrc) cp[s.csrc >>> 0] = s.peer; }); });
      Object.keys(cp).forEach(function (k) { var d = hornDevOf(R, conn, cp[k]); if (d && d !== me_) m[k] = d; });
      conn.csrc = m;
      var key = JSON.stringify(m);
      if (key !== conn.csrcKey) { conn.csrcKey = key; H.X.csrcMap(m, conn.key); }
    });
  }
  function hornConnState(R, conn) {
    var H = R.horn; if (!H || !live(R)) return;
    var ls = Object.keys(conn.legs).map(function (k) { return conn.legs[k]; });
    var up = ls.length && ls.every(function (L) { return L.pc.connectionState === 'connected'; });
    var bad = ls.some(function (L) { return L.pc.connectionState === 'failed' || L.pc.connectionState === 'closed'; });
    if (up && !conn.up) conn.up = Date.now();
    if (conn.onUp && (up || bad)) conn.onUp(!!up);
    drawInfo(R);
  }
  // Neue Verbindung uebernehmen (make-before-break): die bisherige laeuft weiter, bis die neue Rahmen liefert und die
  // anderen ebenfalls gewechselt haben (hornMon schliesst sie)
  function hornAdopt(R, conn, why) {
    var H = R.horn, prev = H.cur, now = Date.now();
    if (H.next === conn) H.next = null;
    H.cur = conn;
    if (prev && prev !== conn) {
      prev.retired = now; H.olds.push(prev);
      // Bild nur noch ueber die neue Verbindung senden (ein Kodierer statt zwei; das Bild kostet beim Wechsel ohnehin ein
      // Schluesselbild), Ton weiter ueber beide, bis die alte schliesst
      Object.keys(prev.legs).forEach(function (k) { var L = prev.legs[k]; [L.cam, L.scr].forEach(function (x) { if (x) x.sender.replaceTrack(null).catch(function () {}); }); });
    }
    if (!H.sw) H.sw = new H.L.Switcher({ node: conn.node, path: conn.path, now: now });
    else H.sw.moved(now, conn.node, conn.path, why);
    if (prev) H.switches.push({ t: now, from: prev.node, to: conn.node, why: why, path: conn.path, fromPath: prev.path });
    if (prev && !H.arT) H.arT = setInterval(function () {   // waehrend des Wechsels: Ton alle 100 ms umhaengen
      if (!live(R) || !R.horn || !H.olds.length) { clearInterval(H.arT); H.arT = null; if (live(R) && R.horn) hornAudioRoute(R); return; }
      hornAudioRoute(R);
    }, 100);
    clearTimeout(H.retryT); H.retryT = null; H.retry0 = 0;
    if (!R.since) { R.since = now; stopTone(); }
    R.connected = true;
    if (R.ui) { R.ui.st.textContent = ''; R.ui.st.hidden = true; }
    H.note = '';
    hornAudioEls(R, conn); hornMap(R); hornDraw(R); hornRunes(R);
    hornStateOut(R);                                   // P9/P10: neue Kennung am Knoten an alle (Zuordnung ueber Kaskade)
    if (!H.monT) H.monT = setInterval(function () { hornMon(R); }, HORN_MON);
    if (!H.spkT) hornSpeakers(R);
    drawInfo(R); drawCtl(R);
  }
  // Wechseln. why: lost | draining | better (opt.to) | udp-loss / udp (opt.path, selber Knoten)
  function hornSwitch(R, why, opt) {
    var H = R.horn; opt = opt || {};
    if (!H || !live(R) || H.switching || R.done) return;
    hornLog(H, H.cur, 'switch ' + why);
    var cur = H.cur, hard = why === 'lost' || why === 'draining';
    H.switching = true;
    if (why === 'lost' && cur) H.bad[cur.node] = Date.now() + HORN_BAD_MS;   // Leeren ist kein Fehler: nur beim Verlassen meiden
    if (hard && cur) H.leaving = { node: cur.node, until: Date.now() + HORN_BAD_MS };
    if (why === 'lost') hornNote(R, T('Verbindung zum Knoten {node} unterbrochen – wechsle …', { node: cur ? cur.node : '?' }));
    else if (why === 'draining') hornNote(R, T('Knoten {node} wird gewartet – wechsle ohne Unterbrechung …', { node: cur ? cur.node : '?' }));
    var p;
    if (opt.path && cur) p = hornFresh(R, cur.cand, []).then(function (c) { return hornConnect(R, c, opt.path); }).then(function (conn) { hornAdopt(R, conn, why); });
    else p = hornCands(R, null, hornAvoid(H)).then(function (list) {
      if (opt.to) list = list.filter(function (c) { return c.node === opt.to; });
      else if (hard && cur) {
        var other = list.filter(function (c) { return c.node !== cur.node; });
        list = other.length ? other : list;            // nur dieser eine Knoten: dorthin neu verbinden
      }
      if (!list.length && hard && cur) list = [cur.cand];
      return hornStart(R, list, why);
    });
    p.then(function () { H.switching = false; }, function (e) {
      H.switching = false;
      if (!live(R) || e === null) return;
      if (e && e.message === T('Der Anruf ist schon beendet.')) { remoteEnded(R, {}); return; }
      if (hard) hornRetry(R, why);                     // sonst bleibt die bisherige Verbindung
    });
  }
  // Kein Knoten erreichbar: klare Meldung, erneut versuchen (Backoff bis 15 s), nach 2 min aufgeben
  function hornRetry(R, why) {
    var H = R.horn; if (!H || !live(R)) return;
    if (!H.retry0) H.retry0 = Date.now();
    if (Date.now() - H.retry0 > HORN_RETRY_MAX) { fail(R, T('Verbindung zum Knoten verloren.')); return; }
    var n = (H.retryN = (H.retryN || 0) + 1), s = Math.min(15, 2 * n);
    // Nr. 119: Notaus bzw. erschoepftes Kontingent beim anderen Server - Wiederholen hilft nicht (Notaus) bzw. selten
    // (Kontingent: hoechstens drei Versuche), statt bis zu 2 min "neuer Versuch" zu zeigen
    if (H.fedErr && (H.fedWhy === 'off' || H.fedWhy === 'local_off' || (H.fedWhy === 'quota' && n > 3))) { fail(R, H.fedErr); return; }
    hornNote(R, (H.fedErr ? H.fedErr + ' ' : '') + T('Kein Knoten erreichbar – neuer Versuch in {s} s …', { s: s }));
    clearTimeout(H.retryT);
    H.retryT = setTimeout(function () {
      if (!live(R)) return;
      H.bad = {};
      hornCands(R, null, []).then(function (list) { return hornStart(R, list, H.cur ? 'lost' : why === 'join' ? 'join' : 'retry'); })
        .catch(function (e) { if (live(R) && e !== null) { hornErr(H, '', e); hornRetry(R, why); } });
    }, s * 1000);
  }
  // Neuzeichnen gebuendelt (Meldungen des Workers kommen je Empfaenger einzeln)
  function hornDrawSoon(R) {
    var H = R.horn; if (!H || H.drawT) return;
    H.drawT = setTimeout(function () { H.drawT = null; if (live(R) && R.horn === H) hornDraw(R); }, 80);
  }
  function hornConnClose(R, conn) {
    if (!conn || conn.dead) return;
    conn.dead = true;
    var H = R.horn;
    Object.keys(conn.legs).forEach(function (role) {
      var L = conn.legs[role];
      if (L.ws) { try { L.ws.close(); } catch (e) { /* zu */ } L.ws = null; }
      if (L.id) hornFetch(conn.url + '/api/sfu/leg/' + encodeURIComponent(L.id), null, 'DELETE').catch(function () {});
      try { L.pc.close(); } catch (e) { /* zu */ }
      if (H) (L.recv || []).forEach(function (x) { delete H.vown[x.rid]; });
    });
    conn.audios.forEach(function (a) { a.srcObject = null; a.remove(); });
    conn.audios = [];
    if (H && H.X) H.X.csrcDrop(conn.key);
    if (H) { H.olds = H.olds.filter(function (x) { return x !== conn; }); if (H.cur === conn) H.cur = null; if (H.next === conn) H.next = null; }
  }

  // --- Laufende Beobachtung (alle 0,5 s): Verbindung, Wechsel, Ton, Sprecher, Ping, Qualitaet --------------------------
  function hornMon(R) {
    var H = R.horn; if (!H || !live(R) || R.done) { if (H) clearInterval(H.monT); return; }
    var now = Date.now(), cur = H.cur;
    if (cur && !H.switching) {
      var worst = 'connected';
      Object.keys(cur.legs).forEach(function (k) {
        var s = cur.legs[k].pc.connectionState;
        if (s === 'failed' || s === 'closed') worst = s; else if (s !== 'connected' && worst === 'connected') worst = 'disconnected';
      });
      var d = H.sw.conn(now, worst);
      if (d) { hornLog(H, cur, 'ice ' + worst); hornSwitch(R, d.why); return; }
    }
    // Neues Bein liefert Rahmen? (RTP-Quellen der Tonplaetze, ersatzweise 1,5 s nach dem Verbinden)
    hornConns(R).forEach(function (c) {
      if (c.rx || !c.up) return;
      var got = Object.keys(c.legs).some(function (k) {
        return (c.legs[k].aud || []).concat(c.legs[k].vid || []).some(function (x) { return x.receiver.getSynchronizationSources && x.receiver.getSynchronizationSources().length; });
      });
      if (got || now - c.up > 1500) c.rx = now;
    });
    // Alte Verbindungen schliessen (make-before-break): 2 s nachdem die neue liefert, wenn die alte nichts Eigenes mehr
    // bringt - kein Absender, den man nur dort hoert (der neue Knoten vergibt seine Tonplaetze erst nach ein paar
    // Takten) - und man selbst nicht gerade spricht, waehrend am alten Knoten noch andere zuhoeren. Hoechstens 15 s.
    if (H.olds.length && cur && cur.rx) hornSelfLevel(R, cur);
    H.olds.slice().forEach(function (o) {
      if (!cur || !cur.rx) return;
      var onCur = {};
      cur.audios.forEach(function (a) { var d = hornAudioOwner(cur, a._i); if (d && hornAudioLive(a, d)) onCur[d] = true; });
      var uniq = o.audios.some(function (a) {
        var d = hornAudioOwner(o, a._i), src = a._tr.receiver.getSynchronizationSources ? a._tr.receiver.getSynchronizationSources()[0] : null;
        return d && !onCur[d] && src && (H.levels || {})[d] > 0.02;
      });
      var mine = hornMyRef(R, o), listeners = Object.keys(o.peers || {}).filter(function (ref) { return ref !== mine; }).length;
      var need = uniq || (listeners > 0 && (H.selfLevel || 0) > 0.02);
      var dead = Object.keys(o.legs).some(function (k) { var s = o.legs[k].pc.connectionState; return s === 'failed' || s === 'closed'; });
      if (dead || o.closed || H.L.closeOld(now, Math.max(cur.rx, o.retired || 0), need ? 1 : 0)) { hornLog(H, o, 'zu' + (need ? ' (15 s)' : '') + ' uniq=' + uniq + ' hoerer=' + listeners + ' selbst=' + Math.round((H.selfLevel || 0) * 100) / 100); hornConnClose(R, o); hornDraw(R); }
    });
    hornAudioRoute(R);
    if (H.olds.length || H.next) hornDraw(R);           // Wechsel: Kacheln folgen den Rahmen der neuen Verbindung
    if (cur && cur.ws && cur.ws.readyState === 1 && now - (cur.pingAt || 0) >= HORN_PING) {
      cur.pingAt = now; cur.pingN++; cur.pings[cur.pingN] = performance.now(); delete cur.pings[cur.pingN - 10];   // verlorene Pongs nicht sammeln
      try { cur.ws.send(JSON.stringify({ t: 'ping', n: cur.pingN })); } catch (e) { /* zu */ }
    }
    if (cur && now - H.statT >= 3000) { H.statT = now; hornStats(R, cur); }
    // Auf TCP/TLS zu einem Knoten mit UDP: alle 10 s die UDP-Probe (Rueckkehr auf UDP nach 30 s ok, hoechstens 1x je 5 min)
    if (cur && cur.path !== 'udp' && cur.cand.udp && cur.cand.tokens && cur.cand.tokens.both && now - (H.udpProbeAt || 0) >= 10000) {
      H.udpProbeAt = now;
      hornProbe(cur.cand, window.SagaHorn.K.PROBE_MS).then(function (m) { if (live(R) && H.cur === cur) H.meas[cur.node] = m; });
    }
    // Andere Kandidaten (mit Kaskade, P9): alle 30 s frische Liste, messen, Hysterese entscheidet
    if (cur && !H.switching && now - (H.candT || cur.created) >= HORN_CANDS_EVERY) {
      H.candT = now;
      hornCands(R, null, []).then(function (list) {
        var others = list.filter(function (c) { return c.node !== cur.node; });
        if (!others.length || !live(R)) return;
        return hornMeasure(R, list).then(function (m) {
          var curScore = H.L.score({ ok: true, rtts: cur.rtts.length ? cur.rtts : (m[cur.node] || {}).rtts, udp: cur.path === 'udp' ? 1 : null },
                                   { here: true, udp: cur.path === 'udp', load: (list.filter(function (c) { return c.node === cur.node; })[0] || {}).load });
          var x = H.sw.observe(Date.now(), curScore, others.map(function (c) { return { node: c.node, score: H.L.score(m[c.node], c, true) }; }));
          if (x) hornSwitch(R, 'better', { to: x.to });
        });
      }).catch(function () {});
    }
  }
  // Eigener Sprechpegel (media-source des Ton-Senders) - nur waehrend eines Wechsels gebraucht
  function hornSelfLevel(R, conn) {
    var H = R.horn, L = conn.aLeg || conn.legs.audio || conn.legs.both;
    if (!L || !L.tr || H.selfT) return;
    H.selfT = true;
    L.tr.sender.getStats().then(function (st) {
      var lv = 0; st.forEach(function (x) { if (x.type === 'media-source' && x.kind === 'audio' && typeof x.audioLevel === 'number') lv = x.audioLevel; });
      H.selfLevel = R.mic ? lv : 0;
    }).catch(function () {}).then(function () { H.selfT = false; });
  }
  // Qualitaet (RTT, Verlust, Einfrieren) und UDP-Regeln aus getStats
  function hornStats(R, conn) {
    var H = R.horn, legs = Object.keys(conn.legs).map(function (k) { return conn.legs[k]; });
    Promise.all(legs.map(function (L) { return L.pc.getStats().catch(function () { return null; }); })).then(function (all) {
      if (!live(R) || conn.dead) return;
      var rtt = null, lost = 0, recv = 0, fz = 0;
      all.forEach(function (st) {
        if (!st) return;
        st.forEach(function (s) {
          if (s.type === 'candidate-pair' && s.nominated && s.state === 'succeeded' && typeof s.currentRoundTripTime === 'number') rtt = Math.max(rtt || 0, s.currentRoundTripTime * 1000);
          if (s.type === 'inbound-rtp') { lost += s.packetsLost || 0; recv += s.packetsReceived || 0; if (s.kind === 'video') fz += s.freezeCount || 0; }
        });
      });
      var p = conn.q5 || { lost: lost, recv: recv, fz: fz, t: Date.now() };
      var dl = lost - p.lost, dr = recv - p.recv, loss = dl + dr > 0 ? Math.max(0, dl) / (dl + dr) : 0;
      var mins = Math.max(1 / 60, (Date.now() - p.t) / 60000), fzm = (fz - p.fz) / mins;
      conn.q5 = { lost: lost, recv: recv, fz: fz, t: Date.now() };
      if (rtt == null && conn.rtts.length) rtt = H.L.median(conn.rtts);
      conn.net = { rtt: rtt == null ? null : Math.round(rtt), loss: Math.round(loss * 1000) / 1000, freezes: Math.round(fzm * 10) / 10 };
      conn.q = H.L.quality(conn.net);
      if (conn === H.cur && !H.switching) {
        var udpOk = conn.path !== 'udp' && conn.cand.udp && (H.meas[conn.node] || {}).udp != null;
        var x = H.sw.udp(Date.now(), loss, udpOk);
        if (x && x.path !== conn.path && (x.path !== 'udp' || conn.cand.tokens)) hornSwitch(R, x.why, { path: x.path });
      }
      drawInfo(R);
    });
  }
  // Tonplaetze je Verbindung: eigenes <audio> je Platz. Waehrend eines Wechsels spielt je Absender nur EINE Verbindung:
  // die neue, sobald sie Rahmen liefert und der Absender dort einen Tonplatz hat, sonst die alte (keine Luecke, kein Doppel)
  function hornAudioEls(R, conn) {
    var a, H = R.horn;
    Object.keys(conn.legs).forEach(function (k) {
      (conn.legs[k].aud || []).forEach(function (tr, i) {
        if (conn.audios.some(function (x) { return x._tr === tr; })) return;
        a = el('audio', 'cl-raudio'); a.autoplay = true; a.dataset.slot = String(i); a.dataset.conn = conn.key; a._tr = tr; a._i = i;
        if (R.ui) R.ui.w.appendChild(a);
        if (R.sinkId && a.setSinkId) a.setSinkId(R.sinkId).catch(function () {});
        a.srcObject = new MediaStream([tr.receiver.track]); a.muted = !R.spkOut;
        var p = a.play(); if (p && p.catch) p.catch(function () { R.audioBlocked = true; drawInfo(R); });
        conn.audios.push(a); conn.aLeg = conn.legs[k];
      });
    });
    if (H) hornAudioRoute(R);
  }
  function hornAudioOwner(conn, i) {
    var L = conn.aLeg, s = L && (L.slots || []).filter(function (x) { return x.class === 'audio'; })[i];
    return s && s.csrc ? conn.csrc[s.csrc >>> 0] || null : null;
  }
  // Liefert dieser Tonplatz seit mindestens 400 ms Rahmen? (Jitter-Puffer gefuellt - erst dann die alte Verbindung fuer
  // diesen Absender stumm schalten, sonst entstuende eine Luecke)
  // Je (Platz, Absender): ein Platz, der eben noch eine andere Person trug, zaehlt fuer die neue erst nach 400 ms.
  function hornAudioLive(a, owner) {
    var src = a._tr.receiver.getSynchronizationSources ? a._tr.receiver.getSynchronizationSources()[0] : null, pnow = performance.now();
    // neuer Weg zaehlt erst nach 1 s ohne Aussetzer (Ton alle 20 ms; 250 ms ohne Paket = Aussetzer, dann spielt das
    // alte Bein wieder) - so bleibt die Luecke beim Wechsel auch auf langsamen Geraeten klein
    if (!(src && (!src.timestamp || pnow - src.timestamp < 250))) { a._since = 0; return false; }
    if (a._owner !== owner) { a._owner = owner; a._since = pnow; }
    if (!a._since) a._since = pnow;
    return pnow - a._since >= 1000;
  }
  function hornAudioRoute(R) {
    var H = R.horn, cur = H.cur, onCur = {};
    if (cur) cur.audios.forEach(function (a) { var d = hornAudioOwner(cur, a._i); if (d && hornAudioLive(a, d)) onCur[d] = true; });
    hornConns(R).forEach(function (c) {
      var seen = {};                                   // P9: derselbe Absender zweimal am Knoten (direkt und ueber eine Bruecke)
      c.audios.forEach(function (a) {
        var ow = hornAudioOwner(c, a._i), dup = (c !== cur && onCur[ow]) || (ow && seen[ow]);
        if (ow) seen[ow] = true;
        var m = !R.spkOut || !!dup;
        a.dataset.owner = ow ? ow.slice(0, 8) : '';      // Diagnose (Tests)
        if (a.muted !== m) a.muted = m;
        if (!m && a.paused) a.play().catch(function () {});
      });
    });
  }
  function hornSpk(R) { if (R.horn) hornAudioRoute(R); }
  function hornCam(R) {
    if (!R.horn) return;
    hornConns(R).forEach(function (c) { Object.keys(c.legs).forEach(function (k) { var L = c.legs[k]; if (L.cam) L.cam.sender.replaceTrack(R.cam || null).catch(function () {}); }); });
    hornStateOut(R); hornDraw(R);
  }
  function hornMic(R) {
    if (!R.horn) return;
    var t = hornAudioTrack(R);
    hornConns(R).forEach(function (c) { Object.keys(c.legs).forEach(function (k) { var L = c.legs[k]; if (L.tr && L.tr.sender.track !== t) L.tr.sender.replaceTrack(t).catch(function () {}); }); });
    hornStateOut(R); hornList(R);
  }
  // Bildschirm ueber den Knoten: eigener Sender je Bild-Leitung (Platz "screen" bei den anderen)
  function hornScreen(R) {
    if (!R.horn) return;
    hornConns(R).forEach(function (c) {
      Object.keys(c.legs).forEach(function (k) {
        var L = c.legs[k]; if (!L.scr) return;
        if (R.screen) { try { R.screen.contentHint = 'detail'; } catch (e) { /* aelterer Browser */ } }
        L.scr.sender.replaceTrack(R.screen || null).catch(function () {});
      });
    });
    hornMic(R);                                        // Systemton (gemischt) bzw. wieder nur das Mikrofon
    hornStateOut(R); hornDraw(R);
  }

  // --- Ansicht: Kacheln bis 25, Last-N, Sprecheransicht, Bildschirm --------------------------------------------------------
  // Quellen je Geraet: Bild aus dem Platz, den ein Knoten ihm gerade gibt (aktuelle Verbindung zuerst), Bildschirm aus dem
  // Bildschirmplatz, Sprecher aus dem Sprecherplatz
  function hornVideoLive(tr) {
    var src = tr.receiver.getSynchronizationSources ? tr.receiver.getSynchronizationSources()[0] : null;
    return !!(src && (!src.timestamp || performance.now() - src.timestamp < 700));
  }
  function hornSources(R) {
    var H = R.horn, out = { vid: {}, scr: null, spk: null }, cur = H.cur;
    // Waehrend eines Wechsels: Bild aus der neuen Verbindung erst, wenn dort Rahmen ankommen (sonst die alte weiter)
    var conns = hornConns(R).sort(function (a, b) { return (b === cur) - (a === cur); });
    conns.forEach(function (c) {
      Object.keys(c.legs).forEach(function (k) {
        var L = c.legs[k];
        (L.slots || []).forEach(function (s) {
          if (s.class === 'audio' || !s.csrc || s.paused) return;
          var d = c.csrc[s.csrc >>> 0], tr = L.recv[s.i];
          if (!d || !tr) return;
          // Bild: das Geraet, dessen Rahmen der Empfaenger gerade wirklich bekommt (Wechsel am Knoten erst am Schluesselbild)
          var vd = tr.rid && H.vown[tr.rid];
          if (s.class === 'screen') { if (!out.scr && (!vd || vd === d) && (H.st[d] || {}).screen !== false) out.scr = { dev: d, track: tr.receiver.track }; return; }
          if (s.class === 'speaker' && (!out.spk || c === cur)) out.spk = d;
          if (vd) d = vd;
          if (conns.length > 1 && !hornVideoLive(tr)) return;
          if (!out.vid[d]) out.vid[d] = { track: tr.receiver.track, cls: s.class, conn: c };
        });
      });
    });
    return out;
  }
  function hornTileEl(R, x) {
    var box = el('div', 'cl-tile'); box.setAttribute('role', 'listitem'); box.dataset.dev = x.device;
    var v = el('video', 'cl-tv'); v.autoplay = true; v.muted = true; v.playsInline = true; v.setAttribute('playsinline', '');
    var still = el('canvas', 'cl-still'); still.width = 320; still.height = 180; still.setAttribute('aria-hidden', 'true');
    var p = personOf(x);
    var av = HK.avatarImg(el('span', 'cl-av', (p.name || '?').charAt(0).toUpperCase()), x.avatar || null, 56); av.setAttribute('aria-hidden', 'true');
    var lab = el('div', 'cl-tl'), tm = el('span', 'cl-tm'); tm.appendChild(icon('micOff')); lab.appendChild(tm); lab.appendChild(el('b', null, shortName(p.name || '?', 15))); lab.appendChild(el('span', 'cl-td', x.dev_name || ''));
    [v, still, av, lab].forEach(function (y) { box.appendChild(y); });
    box.addEventListener('dblclick', function () { var H = R.horn; if (H) { H.pin = H.pin === x.device ? null : x.device; hornSetView(R, 'speaker'); } });
    v.addEventListener('loadeddata', function () { if (live(R) && R.horn) hornDraw(R); });   // erstes Bild da: zeigen
    return { box: box, v: v, still: still, hasStill: false, track: null };
  }
  function hornDraw(R) {
    var H = R.horn; if (!H || !R.ui || R.done || !H.L) return;
    var me_ = myDev().id, src = hornSources(R), devs = [], moving = {}, w_;
    (R.legs || []).forEach(function (x) { if (x.device !== me_) devs.push(x.device); });
    devs.forEach(function (d) { if (src.vid[d]) moving[d] = true; });
    H.spk = src.spk || H.spk;
    if (H.spk && devs.indexOf(H.spk) < 0) H.spk = null;
    var lay = H.L.tiles(devs, moving, H.pin && devs.indexOf(H.pin) >= 0 ? H.pin : H.spk, H.view), want = {};
    // Streifen (Sprecheransicht bzw. Bildschirm): oben gross (Sprecher-Kachel bzw. Bildschirm), unten die anderen
    var strip = !!src.scr || (H.view === 'speaker' && lay.list.length > 1), pos = 0;
    var hb = R.ui.hbig;
    if (!hb) { hb = R.ui.hbig = el('div', 'cl-hbig'); R.ui.grid.parentNode.insertBefore(hb, R.ui.grid); }
    lay.list.forEach(function (t) {
      var x = legOf(R, t.dev); if (!x) return;
      want[t.dev] = true;
      var T_ = H.tiles[t.dev];
      if (!T_) T_ = H.tiles[t.dev] = hornTileEl(R, x);
      var s = H.st[t.dev] || {}, vs = src.vid[t.dev], tr = vs ? vs.track : null;
      if (tr !== T_.track) {
        // Platz weg (Last-N) bzw. anderer Platz/Knoten (Wechsel): letztes Bild als Standbild, bis neue Rahmen kommen
        if (T_.track && T_.v.videoWidth && s.cam !== false) {
          try { T_.still.getContext('2d').drawImage(T_.v, 0, 0, T_.still.width, T_.still.height); T_.hasStill = true; } catch (e) { T_.hasStill = false; }
        }
        T_.track = tr; T_.v.srcObject = tr ? new MediaStream([tr]) : null; if (tr) T_.v.play().catch(function () {});
      }
      if (s.cam === false) T_.hasStill = false;
      // Rahmen da (sonst bis zum ersten Bild Standbild bzw. Bild der Person). Nr. 067: erst mit echtem Bild (Breite > 0) -
      // vorher bleibt die Kachel dunkel mit Avatar wie im Mesh statt einer leeren hellen Flaeche
      var frames = !!tr && T_.v.readyState >= 2 && T_.v.videoWidth > 0;
      T_.box.classList.toggle('has-video', frames && s.cam !== false);
      T_.box.classList.toggle('still', !frames && T_.hasStill);
      T_.box.classList.toggle('big', !!t.big);
      T_.box.classList.toggle('muted', s.mic === false);
      T_.box.classList.toggle('peer-screen', !!(src.scr && src.scr.dev === t.dev));
      T_.box.classList.toggle('wait', !H.cur);
      T_.box.classList.toggle('speaking', !!H.speaking[t.dev]);
      T_.box.setAttribute('aria-label', (x.name || '?') + (x.dev_name ? ' (' + x.dev_name + ')' : '') + (s.mic === false ? ' – ' + T('stumm') : ''));
      if (strip && t.big && !src.scr) { if (T_.box.parentNode !== hb) hb.appendChild(T_.box); }
      else { if (R.ui.grid.children[pos] !== T_.box) R.ui.grid.insertBefore(T_.box, R.ui.grid.children[pos] || null); pos++; }
    });
    Object.keys(H.tiles).forEach(function (d) { if (!want[d]) { H.tiles[d].v.srcObject = null; H.tiles[d].box.remove(); delete H.tiles[d]; } });
    // 2.5 (ANRUF-UI): ab drei Geraeten ist das eigene Bild eine normale Kachel (kein schwebendes Bild-im-Bild)
    var selfT = (R.legs || []).length >= 3, mineV = R.cam && R.cam.readyState === 'live' ? R.cam : null;
    if (selfT) {
      var ST = H.self;
      if (!ST) {
        ST = H.self = { box: el('div', 'cl-tile cl-me'), v: el('video', 'cl-tv') };
        ST.box.setAttribute('role', 'listitem'); ST.v.autoplay = true; ST.v.muted = true; ST.v.playsInline = true; ST.v.setAttribute('playsinline', '');
        var sav = HK.avatarImg(el('span', 'cl-av', (meName() || '?').charAt(0).toUpperCase()), null, 56); sav.setAttribute('aria-hidden', 'true');
        var sl = el('div', 'cl-tl'), stm = el('span', 'cl-tm'); stm.appendChild(icon('micOff')); sl.appendChild(stm); sl.appendChild(el('b', null, T('Du')));
        [ST.v, sav, sl].forEach(function (y) { ST.box.appendChild(y); });
      }
      if ((ST.v.srcObject && ST.v.srcObject.getVideoTracks()[0]) !== mineV) { ST.v.srcObject = mineV ? new MediaStream([mineV]) : null; if (mineV) ST.v.play().catch(function () {}); }
      ST.box.classList.toggle('has-video', !!mineV);
      ST.box.classList.toggle('muted', !R.mic);
      ST.box.classList.toggle('speaking', !!R.mic && (H.selfLevel || 0) > 0.03);
      ST.box.setAttribute('aria-label', T('Du') + (!R.mic ? ' – ' + T('stumm') : ''));
      if (R.ui.grid.children[pos] !== ST.box) R.ui.grid.insertBefore(ST.box, R.ui.grid.children[pos] || null);
      pos++;
    } else if (H.self) { H.self.v.srcObject = null; H.self.box.remove(); H.self = null; }
    w_ = R.ui.w; w_.classList.toggle('horn-self', selfT && !R.screen);
    if (lay.more) {
      if (!H.more) H.more = el('div', 'cl-tile cl-more');
      H.more.textContent = '+' + lay.more; H.more.title = T('{n} weitere Teilnehmende', { n: lay.more });
      R.ui.grid.appendChild(H.more);
    } else if (H.more) { H.more.remove(); H.more = null; }
    var n = lay.list.length + (selfT ? 1 : 0) + (lay.more ? 1 : 0), w = R.ui.w, cols = H.L.cols(n, window.innerWidth);
    w.classList.add('mesh', 'horn');
    w.classList.toggle('tiles', n > 0);
    w.classList.toggle('horn-strip', strip);
    w.classList.toggle('horn-scr', !!src.scr);
    R.ui.grid.dataset.n = String(Math.min(n, 6));
    R.ui.grid.dataset.cols = String(cols);
    R.ui.grid.dataset.scroll = !strip && Math.ceil(n / cols) > (window.innerWidth <= 640 ? 4 : 5) ? '1' : '0';
    R.ui.grid.style.setProperty('--cols', String(cols));
    // Bildschirm gross (eigenes Element), sonst Sprecherbild fuer Bild-im-Bild
    var big = src.scr ? src.scr.track : (H.spk && src.vid[H.spk] ? src.vid[H.spk].track : (devs.length && src.vid[devs[0]] ? src.vid[devs[0]].track : null));
    var hs = R.ui.hs;
    if (!hs) { hs = R.ui.hs = el('video', 'cl-hscreen'); hs.autoplay = true; hs.muted = true; hs.playsInline = true; hs.setAttribute('playsinline', ''); R.ui.grid.parentNode.insertBefore(hs, R.ui.grid); }
    var st = src.scr ? src.scr.track : null;
    if ((hs.srcObject && hs.srcObject.getVideoTracks()[0]) !== st) { hs.srcObject = st ? new MediaStream([st]) : null; if (st) hs.play().catch(function () {}); }
    if ((R.ui.rv.srcObject && R.ui.rv.srcObject.getVideoTracks()[0]) !== big) R.ui.rv.srcObject = big ? new MediaStream([big]) : null;
    hornShare(R, src.scr);
    drawPeer(R); pipBtn(R, !!big); pipDraw(R);
  }
  // Leiste beim Teilen (wie 2.4): eigene (rot, Beenden) bzw. "… teilt den Bildschirm"
  function hornShare(R, scr) {
    var b = R.ui && R.ui.share; if (!b) return;
    b.textContent = ''; b.classList.toggle('me', !!R.screen); b.classList.toggle('cap', !R.screen && !!scr);
    if (R.screen) {
      b.appendChild(el('span', 'cl-share-dot'));
      b.appendChild(el('span', 'cl-share-t', R.mix ? T('Du teilst deinen Bildschirm und den Ton') : T('Du teilst deinen Bildschirm')));
      b.appendChild(btn(T('Teilen beenden'), 'cl-pill cl-share-stop', function () { if (live(R) && R.screen) stopScreen(R); }, T('Teilen beenden')));
      b.hidden = false;
    } else if (scr) {
      b.appendChild(el('span', 'cl-share-t', T('{name} teilt den Bildschirm', { name: hornDevName(R, scr.dev) })));
      b.hidden = false;
    } else b.hidden = true;
  }
  function hornSetView(R, v) {
    var H = R.horn; if (!H) return;
    H.view = v === 'speaker' ? 'speaker' : 'grid';
    if (H.view === 'grid') H.pin = null;
    try { localStorage.setItem('saga-horn-view', H.view); } catch (e) { /* privat */ }
    hornDraw(R); drawCtl(R);
  }
  // Sprechring: Pegel der Tonplaetze (RTP-Quelle), Absender ueber die CSRC des Platzes - 3x je Sekunde
  function hornSpeakers(R) {
    var H = R.horn; clearInterval(H.spkT);
    H.spkT = setInterval(function () {
      if (!live(R) || !R.horn) { clearInterval(H.spkT); return; }
      var now = Date.now(), pnow = performance.now(), lv = {};
      hornConns(R).forEach(function (c) {
        c.audios.forEach(function (a) {
          var d = hornAudioOwner(c, a._i), src = a._tr.receiver.getSynchronizationSources ? a._tr.receiver.getSynchronizationSources()[0] : null;
          // timestamp: performance.now()-Zeitbasis; aelter als 1 s = der Platz liefert gerade nichts
          if (src && typeof src.audioLevel === 'number') H.lvSeen = true;
          if (d && src && typeof src.audioLevel === 'number' && !(src.timestamp && pnow - src.timestamp > 1000)) lv[d] = Math.max(lv[d] || 0, src.audioLevel);
        });
      });
      H.levels = lv;
      if (!H.lvSeen && H.spk) lv[H.spk] = 1;           // Browser ohne Pegel: Sprechring am Sprecherplatz des Knotens
      var ch = false;
      Object.keys(H.tiles).forEach(function (d) {
        var on = (lv[d] || 0) > 0.03 && (H.st[d] || {}).mic !== false;
        if (on) H.speakAt = H.speakAt || {}, H.speakAt[d] = now;
        var keep = on || (H.speakAt && now - (H.speakAt[d] || 0) < 600);
        if (!!H.speaking[d] !== !!keep) { ch = true; if (keep) H.speaking[d] = true; else delete H.speaking[d]; }
        H.tiles[d].box.classList.toggle('speaking', !!keep);
      });
      // Teilnehmerliste: nur den Sprechring umschalten, nicht neu zeichnen (sonst gehen Klicks auf ihre Knoepfe verloren)
      if (ch && H.plist && R.ui && R.ui.w) R.ui.w.querySelectorAll('.cl-plist .cl-pl[data-dev]').forEach(function (li) { li.classList.toggle('speaking', !!H.speaking[li.dataset.dev]); });
    }, 330);
  }
  // --- Kopfzeile: Verschluesselung, Dauer, Zahl, Weg mit Qualitaetspunkt, Runensiegel --------------------------------------
  // 2.5 (ANRUF-UI): Schloss-Symbol + Text (kein Emoji)
  function lockEl(txt) { var s_ = el('span', 'cl-lock'); s_.appendChild(icon('lock')); s_.appendChild(document.createTextNode(txt)); return s_; }
  // Pseudonym-Handles (~abc…) in der Mitte kuerzen, normale Namen kuerzt das CSS am Ende
  function shortName(n, max) { max = max || 18; n = n || ''; if (n.charAt(0) !== '~' || n.length <= max) return n; var k = Math.floor((max - 1) / 2); return n.slice(0, k) + '…' + n.slice(n.length - (max - 1 - k)); }
  function hornInfo(R) {
    var i = R.ui.info, H = R.horn; i.textContent = '';
    if (R.companion) i.appendChild(el('span', 'cl-chip', T('Begleitgerät')));
    // 2.5 (ANRUF-UI): Titel, darunter die Dauer, dann eine Sicherheitszeile
    var hd = el('span', 'cl-head');
    hd.appendChild(el('b', 'cl-title', shortName(R.dm ? ((R.peer && (R.peer.name || R.peer.username)) || R.title) : (R.title || T('Gruppenanruf')), 32)));
    if (R.since) hd.appendChild(el('span', 'cl-dur', dur((Date.now() - R.since) / 1000)));
    i.appendChild(hd);
    if (!R.dm) {
      var nd = (R.legs || []).length || 1, lim = R.limit || 0, cnt = el('span', 'cl-count cl-chip');
      cnt.appendChild(icon('users')); cnt.appendChild(document.createTextNode(lim && nd >= lim - 2 ? nd + '/' + lim : String(nd)));
      cnt.title = T('{n} Geräte', { n: nd }); cnt.setAttribute('aria-label', cnt.title);
      i.appendChild(cnt);
    }
    i.appendChild(lockEl(T('Ende-zu-Ende verschlüsselt')));
    i.lastChild.title = T('Ton und Bild sind je Rahmen mit dem Schlüssel des sendenden Geräts verschlüsselt (saga-horn/v1). Der Knoten leitet nur weiter und kann nichts entschlüsseln.');
    var cur = H.cur;
    if (cur && H.L) {
      var pn = H.L.pathName(cur.path), q_ = cur.q || 'good', net = cur.net || {};
      var pth = el('span', 'cl-path cl-hpath cl-q cl-q-' + q_, pn);
      var qt = q_ === 'good' ? T('gute Verbindung') : q_ === 'mid' ? T('mittlere Verbindung') : T('schwache Verbindung');
      var port = '443'; try { port = new URL(cur.url).port || '443'; } catch (e) { /* ohne Adresse: Standard */ }   // tatsaechlicher Port des Knotens
      pth.title = T('Weg zum Knoten {node}: {path}', { node: cur.node, path: cur.path === 'udp' ? T('UDP (schnellster Weg)') : cur.path === 'turn' ? T('TLS über Port {port} (Rückfall)', { port: port }) : T('TCP über Port {port}', { port: port }) })
        + ' · ' + qt + (net.rtt != null ? ' · RTT ' + net.rtt + ' ms' : '') + (net.loss ? ' · ' + T('Verlust {p} %', { p: Math.round(net.loss * 1000) / 10 }) : '');
      pth.setAttribute('aria-label', pth.title);
      i.appendChild(pth);
      if (H.next || H.olds.length) i.appendChild(el('span', 'cl-path cl-hmove', T('wechselt den Knoten …')));
    }
    if (H.runes) {
      var rb = btn(H.runes.runes, 'cl-runes', function () {
        H.rpop = !H.rpop; rb.setAttribute('aria-expanded', String(H.rpop));
        if (H.rpop && H.plist) { H.plist = false; hornList(R); drawCtl(R); }    // nie zwei Fenster uebereinander (Nr. 071)
        hornRunePop(R);
      }, T('Runensiegel – zum Vergleichen'));
      rb.setAttribute('aria-expanded', String(!!H.rpop)); rb.lang = 'und';
      i.appendChild(rb);
    }
    if (R.ui.un) R.ui.un.hidden = !R.audioBlocked;
  }
  // Runensiegel: 6 Runen aus SHA-256(call_id ‖ sortierte Geraeteschluessel aller aktiven Endpunkte) - aendert sich bei
  // jedem Beitritt und Abgang. Alle sehen dieselben Runen = genau diese Geraete sind im Anruf.
  function hornRunes(R) {
    var H = R.horn; if (!H || !H.X || !R.id) return;
    var me_ = myDev(), devs = (R.legs || []).map(function (x) { return x.device; }).sort();
    Promise.all((R.legs || []).map(function (x) {
      if (x.device === me_.id) return Promise.resolve(me_.sign_pk);
      var P = hornPeer(R, x);
      return peerKeys(P).then(function () { var d = P.peerDevs[x.device]; return d ? d.sign_pk : null; }).catch(function () { return null; });
    })).then(function (pks) {
      var ok = pks.filter(Boolean), key = devs.join(',') + '|' + ok.length;
      if (!live(R) || key === H.runesKey) return;
      H.unverified = (R.legs || []).filter(function (x, i) { return !pks[i]; }).map(function (x) { return x.device; });
      H.runesKey = key;
      return H.X.runes(R.id, ok).then(function (r) { if (live(R)) { H.runes = r; drawInfo(R); hornRunePop(R); hornList(R); } });
    }).catch(function () {});
  }
  function hornRunePop(R) {
    var H = R.horn, w = R.ui && R.ui.w; if (!w) return;
    var p = w.querySelector('.cl-rpop');
    if (!H.rpop || !H.runes) { if (p) p.remove(); return; }
    if (!p) { p = el('div', 'cl-rpop'); p.setAttribute('role', 'dialog'); p.setAttribute('aria-label', T('Runensiegel')); w.appendChild(p); }
    p.textContent = '';
    var hd = el('div', 'cl-rpop-h'); hd.appendChild(el('b', null, T('Runensiegel')));
    hd.appendChild(btn(icon('x'), 'cl-mini', function () { H.rpop = false; hornRunePop(R); drawInfo(R); }, T('Schließen')));
    p.appendChild(hd);
    var big = el('div', 'cl-rpop-r', H.runes.runes); big.lang = 'und'; p.appendChild(big);
    p.appendChild(el('div', 'cl-rpop-n', H.runes.names.join(' · ')));
    p.appendChild(el('p', null, T('Vergleicht die sechs Runen, zum Beispiel durch Vorlesen. Sehen alle dieselben, sind genau die Geräte in der Teilnehmerliste im Anruf – niemand sonst kann mithören, auch nicht der Server oder der Knoten.')));
    p.appendChild(el('p', 'cl-rpop-s', T('Die Runen ändern sich, sobald jemand beitritt oder geht.')));
    if ((H.unverified || []).length) p.appendChild(el('p', 'cl-rpop-w', T('Nicht geprüft: {names}', { names: H.unverified.map(function (d) { return hornDevName(R, d); }).join(', ') })));
  }
  // --- Teilnehmerliste (Gastgeber: Stummschalten, Entfernen) ------------------------------------------------------------
  function hornHost(R) { return !!(R.call && (R.call.host || (R.call.peer && R.call.peer.username === meName()))); }
  function hornList(R) {
    var H = R.horn, w = R.ui && R.ui.w; if (!H || !w) return;
    var p = w.querySelector('.cl-plist');
    if (!H.plist) { if (p) p.remove(); return; }
    if (!p) { p = el('div', 'cl-plist'); p.setAttribute('role', 'dialog'); p.setAttribute('aria-label', T('Teilnehmende')); w.appendChild(p); }
    p.textContent = '';
    var legs = R.legs || [], me_ = myDev().id, host = hornHost(R);
    var hd = el('div', 'cl-plist-h'); hd.appendChild(el('b', null, T('Teilnehmende ({n})', { n: legs.length })));
    hd.appendChild(btn(icon('x'), 'cl-mini', function () { H.plist = false; hornList(R); drawCtl(R); }, T('Schließen')));
    p.appendChild(hd);
    var ul = el('ul', 'cl-plist-l');
    legs.forEach(function (x) {
      var li = el('li', 'cl-pl' + (H.speaking[x.device] ? ' speaking' : '')); li.dataset.dev = x.device;
      var mine = x.device === me_, s = mine ? { mic: R.mic } : (H.st[x.device] || {});
      var av = HK.avatarImg(el('span', 'cl-av cl-av-s', (x.name || '?').charAt(0).toUpperCase()), x.avatar || null, 32); av.setAttribute('aria-hidden', 'true');
      var t = el('div', 'cl-pl-t');
      t.appendChild(el('b', null, shortName(x.name || '?', 15) + (mine ? ' (' + T('du') + ')' : '')));
      var sub = [x.dev_name || '', x.role === 'companion' ? T('Begleitgerät') : '', R.call && R.call.peer && x.who === R.call.peer.username ? T('Gastgeber') : '']
        .filter(Boolean).join(' · ');
      if (sub) t.appendChild(el('span', 'cl-td', sub));
      if ((H.unverified || []).indexOf(x.device) >= 0) t.appendChild(el('span', 'cl-pl-w', T('nicht geprüft')));
      li.appendChild(av); li.appendChild(t);
      li.appendChild(el('span', 'cl-pl-m' + (s.mic === false ? ' off' : ''))); if (s.mic === false) li.lastChild.appendChild(icon('micOff'));
      if (s.mic === false) li.lastChild.title = T('stumm');
      if (host && !mine && x.who !== meName()) {
        var b = el('div', 'cl-pl-b');
        if (s.mic !== false) b.appendChild(btn(T('Stummschalten'), 'cl-pill cl-pl-mute', function () { hornMod(R, x, 'mute'); }, T('{name} stummschalten', { name: x.name || '?' })));
        // Entfernen in zwei Schritten ("Wirklich entfernen?", 8 s - 3 bis 4 s waren zum Lesen zu kurz, Nr. 076) - der Zustand
        // ueberlebt das Neuzeichnen der Liste
        var sure = H.rmSure && H.rmSure.dev === x.device && H.rmSure.until > Date.now();
        var rm = btn(sure ? T('Wirklich entfernen?') : T('Entfernen'), 'cl-pill cl-pl-rm' + (sure ? ' sure' : ''), function () {
          if (!(H.rmSure && H.rmSure.dev === x.device && H.rmSure.until > Date.now())) {
            H.rmSure = { dev: x.device, until: Date.now() + 8000 }; hornList(R);
            setTimeout(function () { if (H.rmSure && H.rmSure.dev === x.device && H.rmSure.until <= Date.now()) { H.rmSure = null; hornList(R); } }, 8100);
            return;
          }
          H.rmSure = null;
          hornMod(R, x, 'remove');
        }, T('{name} aus dem Anruf entfernen', { name: x.name || '?' }));
        b.appendChild(rm); li.appendChild(b);
      }
      ul.appendChild(li);
    });
    p.appendChild(ul);
  }
  function hornMod(R, x, action) {
    api('POST', '/api/calls/' + R.id + '/moderate', { device: myDev().id, target: x.device, action: action }).then(function () {
      HK.toast(action === 'mute' ? T('{name} wurde stummgeschaltet.', { name: x.name || '?' }) : T('{name} wurde aus dem Anruf entfernt.', { name: x.name || '?' }));
    }).catch(function (e) { HK.toast(e.message); });
  }
  // Ereignis call.mod (Gastgeber hat dieses Geraet stummgeschaltet bzw. diese Person entfernt)
  function hornModIn(d) {
    var R = C; if (!R || R.id !== d.call_id || R.done) return;
    var me_ = myDev().id;
    if (d.action === 'mute' && d.device === me_) {
      if (R.mic) setMic(R, false);
      // Nr. 074: laenger sichtbar (der Hinweis lag frueher unter dem Anruffenster)
      HK.toast(T('{name} hat dich stummgeschaltet. Du kannst das Mikrofon selbst wieder einschalten.', { name: d.by || '?' }), null, null, 8000);
    } else if (d.action === 'remove') {
      api('GET', '/api/calls/' + R.id + '?device=' + q(me_)).then(function (r) {
        if (live(R) && (r.call.mine || []).indexOf(me_) < 0) hornRemoved(R, d.by);
      }).catch(function () {});
    }
  }
  function hornRemoved(R, by) {
    R.ending = true;
    finish(R, by ? T('{name} hat dich aus dem Anruf entfernt.', { name: by }) : T('Du wurdest aus dem Anruf entfernt.'), 'info');
  }
  function hornClose(R) {
    var H = R.horn; if (!H) return;
    clearInterval(H.monT); clearInterval(H.spkT); clearInterval(H.arT); clearTimeout(H.retryT); H.monT = null; H.arT = null;
    hornConns(R).concat(H.olds).forEach(function (c) { hornConnClose(R, c); });
    H.cur = H.next = null; H.olds = [];
  }
  function hornStop(R) {
    var H = R.horn; if (!H) return;
    hornClose(R);
    clearTimeout(H.ackT); clearTimeout(H.rekeyT); clearTimeout(H.stT);
    Object.keys(H.tiles).forEach(function (d) { H.tiles[d].v.srcObject = null; H.tiles[d].box.remove(); });
    H.tiles = {};
    if (R.ui) {
      var x = R.ui.w.querySelector('.cl-plist'), y = R.ui.w.querySelector('.cl-rpop'); if (x) x.remove(); if (y) y.remove();
      if (R.ui.hs) { R.ui.hs.srcObject = null; R.ui.hs.remove(); R.ui.hs = null; }
      if (R.ui.hbig) { R.ui.hbig.remove(); R.ui.hbig = null; }
      R.ui.w.classList.remove('horn', 'horn-strip', 'horn-scr');
    }
    if (H.X) H.X.close();
  }
  // Vor dem Start eines Gruppenanrufs: kein Knoten bereit und die Gruppe ist groesser als die Mesh-Grenze -> Hinweis
  function hornHintNeeded(chat, video) {
    // Foederierter Chat (Nr. 118): ob ein Knoten bereitsteht, entscheidet das Heim (geteilte Knoten anderer Server, Kaskade);
    // der Server nennt beim Mesh-Rueckfall selbst den Grund (call.horn_mesh) - hier nicht nur die eigenen Knoten befragen
    if (chat && chat.fed) return false;
    var n = chat && chat.members ? chat.members.length : 0, lim = cfg && cfg.limits ? (video ? cfg.limits.endpoints_video : cfg.limits.endpoints_audio) : 0;
    return !!(cfg && feat('horn') && cfg.horn_ready === false && lim && n > lim);
  }
  function hornMeshHint(chat, video) {
    if (!hornHintNeeded(chat, video) || document.querySelector('.cl-hint-page')) return Promise.resolve(!document.querySelector('.cl-hint-page'));
    return new Promise(function (ok) {
      var back = el('div', 'cl-hint-back'), box = el('div', 'cl-hint cl-hint-page'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true');
      box.setAttribute('aria-label', T('Gruppenanruf'));
      box.appendChild(el('b', null, T('Kein Knoten für große Gruppenanrufe bereit')));
      box.appendChild(el('p', null, T('Der Anruf läuft deshalb direkt zwischen den Geräten. Dabei können höchstens {v} Geräte mit Video bzw. {a} nur mit Ton teilnehmen.',
                                       { v: cfg.limits.endpoints_video, a: cfg.limits.endpoints_audio })));
      box.appendChild(el('p', 'cl-hint-s', T('Bitte die Verwaltung, einen Gjallarhorn-Knoten bereitzustellen – dann geht es mit bis zu 12 bzw. 25 Personen mit Video.')));
      var row = el('div', 'cl-hint-b'), done = function (v) { box.remove(); back.remove(); ok(v); };
      row.appendChild(btn(T('Abbrechen'), 'cl-pill cl-hint-no', function () { done(false); }));
      row.appendChild(btn(T('Trotzdem anrufen'), 'cl-pill cl-hint-go', function () { done(true); }));
      box.appendChild(row);
      back.addEventListener('click', function () { done(false); });
      box.addEventListener('keydown', function (e) { if (e.key === 'Escape') done(false); });
      document.body.appendChild(back); document.body.appendChild(box);
      var go = box.querySelector('.cl-hint-go'); if (go) go.focus({ preventScroll: true });
    });
  }

  // 2.5 P10: Knoten eines anderen Servers (Teilen an), den die Seite noch nicht kennt (CSP connect-src stammt vom Laden
  // der Seite) - klar sagen, dass ein Neuladen hilft
  document.addEventListener('securitypolicyviolation', function (e) {
    var R = C, u = String(e.blockedURI || '');
    if (!R || !R.horn || !/^(https|wss):/.test(u) || !/connect-src/.test(e.effectiveDirective || e.violatedDirective || '')) return;
    var host = u.replace(/^[a-z]+:\/\//, '').split('/')[0];
    R.horn.fedErr = T('Der Knoten {node} ist für diese Seite noch nicht freigegeben – bitte die Seite neu laden.', { node: host });
    hornNote(R, R.horn.fedErr);
  });

  // --- Live-Ereignisse ---------------------------------------------------------------------------------------------
  document.addEventListener('hk-live', function (e) {
    var t = e.detail.type, d = e.detail.data || {};
    if (t === 'call.ring') {
      // nachgeholte Ereignisse (Last-Event-ID) koennen alt sein: Zustand pruefen, bevor es klingelt
      api('GET', '/api/calls/' + d.call_id).then(function (r) {
        if (r.call.state === 'ringing' && r.call.role === 'callee') ring(Object.assign({}, d, { expires_in: r.call.expires_in }));
      }).catch(function () {});
    } else if (t === 'call.signal') signalIn(d);
    else if (t === 'call.invite') {                   // 2.4: Gruppenanruf klingelt (grosse Gruppen: still, nur die Leiste)
      if (!(C && C.id === d.call_id)) ring(Object.assign({}, d, { group: true }));
      runCheck();
    } else if (t === 'call.roster') {                 // 2.4: Teilnehmerliste (laufender Anruf, Leiste auf anderen Geraeten)
      if (C && C.id === d.call_id && C.ready) rosterIn(C, d.legs, d);
      runCheck();
    } else if (t === 'call.state') {
      // Gruppenanruf "aktiv" heisst nur: zwei sind dabei - die anderen klingeln weiter; "declined": eigene Einladung
      if (d.state === 'ended' || d.state === 'declined' || (d.state === 'active' && (d.kind !== 'group' || d.dm))) unring(d.call_id);
      if (d.state === 'ended') hideRun(d.call_id);
      runCheck();
      var c = C;
      if (c && c.id === d.call_id) {
        if (d.state === 'ended') remoteEnded(c, d);
        else if (c.kind === 'group' || d.moved || (c.established && d.device && d.device !== c.peerDevice)) { /* 2.4: Raum - regelt die Teilnehmerliste */ }
        else if (d.state === 'active' && c.role === 'caller') {
          c.peerDevice = d.device; stopTone(); status(c, T('Verbinde …'));
          c.connT = setTimeout(function () { if (live(c) && !c.connected) iceFailed(c); }, 30000);
        } else if (d.state === 'active' && c.role === 'callee' && !c.ready && d.device !== myDev().id) {
          finish(c, T('Der Anruf wurde auf einem anderen Gerät angenommen.'), 'info');
        }
      }
    } else if (t === 'call.mod') hornModIn(d);       // 2.5: Gastgeber hat stummgeschaltet bzw. entfernt
    else if (t === 'resync' || t === 'live.hello') pending();   // 2.5 (Nr. 173): auch beim ersten hello (Luecke nach dem Laden)
  });
  // Seite neu geladen/geoeffnet, waehrend es klingelt
  // 2.4: dazu Gruppenanrufe (Einladung klingelt) und die Leiste "Anruf laeuft" (anderes eigenes Geraet, Gruppe im Chat)
  function pending() {
    var t0 = Date.now();
    api('GET', '/api/calls/active').then(function (r) {
      var seen = {};
      (r.calls || []).forEach(function (x) {
        seen[x.id] = true;
        if (x.role === 'callee' && x.state === 'ringing' && !RINGS[x.id] && !(C && C.id === x.id))
          ring({ call_id: x.id, chat_id: x.chat_id, media: x.media, from: x.peer, ring_s: x.ring_s, expires_in: x.expires_in });
        else if (x.kind === 'group' && (x.mine || []).length && RINGS[x.id] && !(C && C.id === x.id)) unring(x.id);   // eigenes Geraet ist dabei
        else if (x.kind === 'group' && x.invite === 'ringing' && !(x.mine || []).length && !RINGS[x.id] && !(C && C.id === x.id)
                 && Date.now() / 1000 - x.created < (x.ring_s || 45))
          ring({ call_id: x.id, chat_id: x.chat_id, media: x.media, from: x.from, group: true, dm: !!x.dm, silent: !!x.silent, ring_s: x.ring_s,
                 expires_in: Math.max(5, x.created + (x.ring_s || 45) - Date.now() / 1000) });
        runBar(x);
      });
      Object.keys(RUN).forEach(function (id) { if (!seen[id]) hideRun(id); });
      // 2.5 (Nr. 173): 1:1-Anruf klingelt hier noch, ist aber vorbei (Ende-Ereignis kam, bevor der Live-Kanal stand) -
      // nur Klingeln, die schon vor dieser Abfrage da waren
      Object.keys(RINGS).forEach(function (id) { var g = RINGS[id]; if (!seen[id] && !g.d.group && g.at < t0) unring(id); });
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
  window.addEventListener('pagehide', function () {
    if (C && C.id && !C.done) api('POST', '/api/calls/' + C.id + '/end', { reason: 'hangup', connected: anyConn(C), device: bridge() ? myDev().id : undefined }).catch(function () {});
  });

  // Chat-Seite: #call-<id>[-audio|-video] (aus einer Mitteilung oder einem anderen Tab) -> annehmen bzw. klingeln lassen
  function route() {
    var j = /^#join-([0-9a-f]{32})-(member|companion|handover)(-video)?$/.exec(location.hash);   // 2.4: beitreten (Leiste, Mitteilung)
    if (j) {
      var k = 0, go2 = function () {
        if (!bridge()) { if (++k < 80) return setTimeout(go2, 250); HK.toast(T('Zum Annehmen erst Chats auf diesem Gerät einrichten bzw. entsperren.')); return; }
        history.replaceState(null, '', location.pathname);
        join(j[1], j[2], !!j[3]);
      };
      go2();
      return;
    }
    var m = /^#call-([0-9a-f]{32})(?:-(audio|video))?$/.exec(location.hash);
    if (!m) return;
    var n = 0, go = function () {
      if (!bridge()) { if (++n < 80) return setTimeout(go, 250); HK.toast(T('Zum Annehmen erst Chats auf diesem Gerät einrichten bzw. entsperren.')); return; }
      if (C && C.id === m[1]) return;
      api('GET', '/api/calls/' + m[1]).then(function (r) {
        var g = r.call.kind === 'group';                // 2.5: Raum-Anruf (auch 1:1 ueber Knoten) = beitreten
        if (m[2]) { if (g) { history.replaceState(null, '', location.pathname); join(m[1], 'member', m[2] === 'video'); } else answer(m[1], m[2] === 'video'); return; }
        if (r.call.state === 'ringing' && r.call.role === 'callee') ring({ call_id: r.call.id, chat_id: r.call.chat_id, media: r.call.media, from: r.call.peer, expires_in: r.call.expires_in });
        else if (g && r.call.state !== 'ended' && r.call.invite === 'ringing') ring({ call_id: r.call.id, chat_id: r.call.chat_id, media: r.call.media, from: r.call.from,
                                                                                      group: true, dm: !!r.call.dm, ring_s: r.call.ring_s, expires_in: r.call.expires_in || r.call.ring_s });
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
    // 2.4 E5: in Gruppen-, freien und Seiten-Chats nur, wenn der Server es meldet (chat.call.group: alle beteiligten Server
    // koennen calls3) und dieser Client "multi" kann
    var grp = !!(chat && chat.kind !== 'dm' && chat.call && chat.call.group && myCaps().indexOf('multi') >= 0);
    var on = !!(chat && cfg && cfg.enabled && window.RTCPeerConnection && (chat.kind === 'dm' ? !((chat.v === 2 || chat.v === 3) && (chat.request || chat.readonly)) : grp));
    a.hidden = v.hidden = !on;
    var ta = grp ? T('Gruppenanruf') : T('Anrufen'), tv = grp ? T('Gruppen-Videoanruf') : T('Videoanruf');
    a.title = ta; a.setAttribute('aria-label', ta); v.title = tv; v.setAttribute('aria-label', tv);
  }
  if (CHATS) {
    var a0 = document.getElementById('ct-call-a'), v0 = document.getElementById('ct-call-v');
    var cur = function () { return window.HKChat && window.HKChat.callApi && window.HKChat.callApi.cur(); };
    if (a0) a0.addEventListener('click', function () { start(cur(), false); });
    if (v0) v0.addEventListener('click', function () { start(cur(), true); });
    config().then(function () { head(cur()); capsReport(); }).catch(function () {});
  }
  // 2.4: Faehigkeiten dieses Geraets dem Server melden (PUT /api/calls/caps) - zu Gruppenanrufen eingeladen wird nur, wer
  // ein Geraet mit "multi" hat. Einmal je Seite, sobald die Chat-Schluessel bereit sind.
  var capsSent = '', capsTry = 0;
  function capsReport() {
    if (!bridge() || !cfg) { if (CHATS && !capsSent && ++capsTry < 120) setTimeout(capsReport, 1500); return; }
    var dv = myDev().id, cs = myCaps().join(',');
    if (capsSent === dv + '|' + cs) return;
    capsSent = dv + '|' + cs;
    api('PUT', '/api/calls/caps', { device: dv, caps: myCaps() }).catch(function () { capsSent = ''; });
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
  // 2.4 E4/E5: Raum - Verbindungen je Geraet, Liste, Ansicht, Begleitgeraet, Sprecher (Tests, Diagnose)
  room: function () {
    var R = C; if (!R) return null;
    return { id: R.id, kind: R.kind, companion: !!R.companion, ho: !!R.ho, inRoom: !!R.inRoom, mic: R.mic, spkOut: R.spkOut, mesh: meshView(R),
             cam: !!(R.cam && R.cam.readyState === 'live'),
             tiles: R.ui ? R.ui.grid.children.length : 0, speaker: R.spk || null, since: R.since || 0, legs: (R.legs || []).map(function (x) { return x.device; }),
             links: links(R).map(function (L) {
               var E = els(L);
               return { device: L.peerDevice, me: !!(L.peer && L.peer.me), name: (L.peer && L.peer.name) || '', connected: L.connected, established: L.established,
                        verified: L.verified, pc: L.peerDevice ? pcId(L) : '', role: L.role, path: L.path, rates: L.applied || {}, bytesIn: L.bytesIn || 0,
                        audio: L.audio || {}, playing: !!(E && E.ra && E.ra.srcObject && !E.ra.paused), muted: !!(E && E.ra && E.ra.muted), quiet: quiet(L),
                        remote: L.remoteState, level: L.level || 0, screen: L.scrMode };
             }) };
  },
  join: join, running: function () { return Object.keys(RUN); },
  // 2.5 Gjallarhorn: Knoten, Weg, Beine, Zuordnung, Kacheln (Tests, Diagnose) und Zaehler des Frame-E2E-Workers
  // P5: dazu Verbindungen je Knoten (Wechsel), Messung, Ansicht, Standbilder, Sprechring, Runensiegel
  horn: function () {
    var R = C; if (!R || !R.horn) return null;
    var H = R.horn, cur = H.cur, legs = {};
    var legsOf = function (c) { var o = {}; Object.keys(c.legs).forEach(function (k) { var L = c.legs[k]; o[k] = { id: L.id, state: L.pc.connectionState, est: L.est || 0, slots: L.slots, ws: !!(L.ws && L.ws.readyState === 1) }; }); return o; };
    if (cur) legs = legsOf(cur);
    var up = !!cur && Object.keys(cur.legs).length > 0 && Object.keys(cur.legs).every(function (k) { return cur.legs[k].pc.connectionState === 'connected'; });
    return { id: R.id, node: cur && cur.node, url: cur && cur.url, path: cur ? cur.path : '', mode: H.X && H.X.mode, up: up, since: R.since || 0,
             csrc: cur ? cur.csrc : {}, mine: cur && cur.mine, key_n: H.key ? H.key.n : null, known: Object.keys(H.known), legs: legs, sent: H.sent || 0,
             hp: (R.legs || []).reduce(function (o, x) { var r = cur ? hornRef(x, cur) : x.hp; if (r) o[x.device] = r; return o; }, {}),
             conns: hornConns(R).map(function (c) { return { key: c.key, node: c.node, path: c.path, cur: c === cur, up: c.up, rx: c.rx, peers: Object.keys(c.peers).length, legs: legsOf(c) }; }),
             switches: H.switches, switching: !!H.switching, meas: H.meas, ranked: H.ranked || null, note: H.note, view: H.view, net: cur && cur.net, q: cur && cur.q,
             runes: H.runes, host: hornHost(R), screen: !!R.screen, spk: H.spk || null, speaking: Object.keys(H.speaking), levels: H.levels || {}, lvSeen: !!H.lvSeen, errs: H.errs || [], bad: H.bad, log: H.log || [],
             tiles: Object.keys(H.tiles).map(function (d) { var x = H.tiles[d]; return { dev: d, video: !!x.track, live: !!(x.track && x.track.readyState === 'live'), still: x.box.classList.contains('still'),
                                                                                         big: x.box.classList.contains('big'), speaking: x.box.classList.contains('speaking'), muted: x.box.classList.contains('muted') }; }),
             shared: (function () { var s = hornSources(R).scr; return s ? { dev: s.dev, live: s.track.readyState === 'live' } : null; })() };
  },
  hornView: function (v) { if (C && C.horn) hornSetView(C, v); },
  hornSwitch: function (why) { if (C && C.horn) hornSwitch(C, why || 'lost'); },
  caps: function () { return config().then(function () { return myCaps(); }); },
  hornStats: function (reset) { return C && C.horn && C.horn.X ? C.horn.X.stats(reset) : Promise.resolve(null); },
  // 2.4: fuer Tests und die Diagnose - ICE neu aushandeln (wie bei einem Netzwechsel), Bild-im-Bild, Regeltabelle
  restartIce: function () { return restartIce(C); }, pip: function (on) { if (!C) return Promise.resolve(false); return on === false ? (pipClose(C), Promise.resolve(true)) : pipOpen(C); },
  rules: rules, limits: { restartMax: RESTART_MAX, discS: DISC_S, failS: FAIL_S } };
})();
