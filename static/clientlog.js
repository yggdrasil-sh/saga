/* Fehlerberichte und "Diagnose senden" (2.5, Feature clientlog1, Vertrag docs/APP-API.md "Fehlerberichte und Diagnose").
   Frueh geladen (vor common.js), ohne Abhaengigkeiten. Sammelt Programmfehler (window error, unhandledrejection) ohne
   Inhalte: Fehlertexte werden bereinigt (Adressen, E-Mail, Zahlen, IDs, Zitate -> Platzhalter), Stacks nur als
   Funktionsname + static/<datei>.js:Zeile:Spalte. Gleiche Fehler werden gezaehlt statt wiederholt, hoechstens ein
   Bericht je 30 s, beim Verlassen der Seite der Rest (fetch keepalive - sendBeacon kann den Kopf X-Saga nicht setzen).
   Gesendet wird nur, wenn der Server clientlog1 meldet. Daneben ein Ringpuffer der letzten 200 Ereignisse im Speicher
   (Fehler, Status je Endpunkt-Muster, Ansichtswechsel, Live-Kanal, Speichern) - verlaesst den Browser nur ueber
   "Diagnose senden" (Einstellungen -> Hilfe, HKLog.diag). Fehler im Logger selbst werden nie gemeldet (keine Schleife). */
(function () {
  'use strict';
  if (window.HKLog) return;
  var T0 = Date.now(), RATE = 30000, ITEMS = 20, RING = 200, MAX_REPORTS = 40;
  var pending = [], byKey = {}, ring = [], lastSent = 0, timer = null, sent = 0, busy = false, off = false, feat = null, ver = '';
  var SID = (function () {
    var a = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789', s = '', r = new Uint8Array(16);
    try { crypto.getRandomValues(r); } catch (e) { for (var i = 0; i < 16; i++) r[i] = Math.floor(Math.random() * 256); }
    for (var j = 0; j < 16; j++) s += a[r[j] % a.length];
    return s;
  })();
  function now() { return Math.max(0, Date.now() - T0); }
  var oFetch = window.fetch ? window.fetch.bind(window) : null;

  // --- Bereinigung (der Server bereinigt noch einmal; hier: so wenig wie moeglich verlassen lassen) ---------------------
  var IDENT = /^[A-Za-z_$][\w$.]{0,39}$/;
  function scrub(s, max) {
    s = String(s == null ? '' : s).slice(0, 4000).replace(/[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩﻿]/g, ' ');
    s = s.replace(/\b[a-z][a-z0-9+.\-]{1,15}:\/\/[^\s"'<>()]*|\bwww\.[^\s"'<>()]+|\b(?:blob|data|javascript|mailto|tel):[^\s"'<>()]*/gi, function (u) {
      var m = /\/static\/([\w.\-\/]{1,80}?\.(?:js|mjs|css|html))(?:\?[^\s:#]*)?(:\d{1,7}:\d{1,8})?(?=$|[?#:])/.exec(u);
      return m ? 'static/' + m[1] + (m[2] || '') : '<url>';
    });
    s = s.replace(/[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+/g, '<email>');
    s = s.replace(/\b(?:hkd?|sga)_[A-Za-z0-9_\-]{6,}|\bBearer\s+\S+/gi, '<secret>');
    s = s.replace(/'([^'\n]{0,200})'|"([^"\n]{0,200})"|`([^`\n]{0,200})`|„([^“”\n]{0,200})[“”]|“([^”\n]{0,200})”|«([^»\n]{0,200})»/g,
      function (m, a, b, c, d, e, f) {
        var x = a != null ? a : b != null ? b : c != null ? c : d != null ? d : e != null ? e : f;
        return x && IDENT.test(x) ? m : m[0] + '<str>' + ('\'"`'.indexOf(m[0]) >= 0 ? m[0] : '');
      });
    s = s.replace(/(^|[^\w.:<])\/[^\s"'<>()]+/g, function (m, pre) { return pre + '<path>'; });
    s = s.replace(/[~@][\w.\-]{2,}/g, '<id>');
    s = s.replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi, '<uuid>');
    s = s.replace(/\b(?:0x)?[0-9a-f]{8,}\b/gi, '<hex>');
    s = s.replace(/static\/[\w.\-\/]+\.(?:js|mjs|css|html)(?::\d{1,7}:\d{1,8})?|(^|[^\w$])\d+(?:[.,]\d+)*/g, function (m, pre) {
      return m.indexOf('static/') === 0 ? m : (pre || '') + '<n>';
    });
    s = s.replace(/\s{2,}/g, ' ').trim();
    return s.slice(0, max || 300);
  }
  var LOC = /(?:[a-z][a-z0-9+.\-]*:\/\/[^\/\s]*)?\/static\/([\w.\-\/]{1,80}?\.m?js)(?:\?[^\s:)]*)?:(\d{1,7}):(\d{1,8})/i;
  function stackOf(st) {
    if (typeof st !== 'string' || !st) return '';
    var out = [], n = 0;
    st.slice(0, 8000).split('\n').some(function (ln) {
      ln = ln.trim(); if (!ln) return false;
      var m = LOC.exec(ln), fn = /^(?:at\s+)?(?:async\s+)?([A-Za-z_$<][\w$.<>]{0,60})\s*(?:\(|@)/.exec(ln), x;
      if (m) x = (fn ? fn[1] + ' ' : '') + 'static/' + m[1] + ':' + m[2] + ':' + m[3];
      else if (/:\/\/|\.m?js\b/.test(ln)) x = (fn ? fn[1] + ' ' : '') + '<ext>';
      else if (!out.length) x = scrub(ln, 200);      // erste Zeile: "TypeError: ..." (bereinigt)
      else return false;
      if (n + x.length + 1 > 2000) return true;
      out.push(x); n += x.length + 1;
      return out.length >= 20;
    });
    return out.join('\n');
  }
  function srcOf(u) {
    if (!u) return '';
    var m = /\/static\/([\w.\-\/]{1,80}?\.m?js)(?:[?#].*)?$/.exec(String(u));
    return m ? 'static/' + m[1] : '<ext>';
  }
  // Endpunkt-Muster: /api/notes/123/comments?x -> /api/notes/:id/comments (alles, was nicht wie ein Pfadwort aussieht -> :x)
  function pathOf(u) {
    var p = String(u || '');
    try { var a = new URL(p, location.href); if (a.origin !== location.origin) return null; p = a.pathname; } catch (e) { return null; }
    return '/' + p.split('/').slice(1, 12).map(function (s) {
      return /^-?\d+$/.test(s) ? ':id' : /^[a-z][a-z0-9_.\-]{0,31}$/.test(s) ? s : s ? ':x' : '';
    }).join('/');
  }

  // --- grober Ort (kein Titel, keine ID) -------------------------------------------------------------------------------
  function screen() {
    try {
      if (document.querySelector('.cl-win')) return 'calls';
      if (document.querySelector('.hk-dialog-set')) return 'settings';
      var p = location.pathname;
      if (/^\/notes/.test(p)) { var db = document.getElementById('pg-db'); return db && !db.hidden ? 'db-view' : 'pages'; }
      if (/^\/chats/.test(p)) return 'chat';
      if (/^\/security/.test(p)) return 'security';
      if (p === '/') return 'dashboard';
      return 'other';
    } catch (e) { return 'other'; }
  }

  // --- Ringpuffer -----------------------------------------------------------------------------------------------------
  function ev(k, d) {
    if (busy) return;
    try {
      var x = { t: now(), k: k };
      if (d) for (var f in d) if (Object.prototype.hasOwnProperty.call(d, f)) x[f] = d[f];
      ring.push(x); if (ring.length > RING) ring.splice(0, ring.length - RING);
    } catch (e) { /* nie weiterwerfen */ }
  }
  var lastScreen = '';
  function viewCheck() { var s = screen(); if (s !== lastScreen) { lastScreen = s; ev('view', { screen: s }); } }

  // --- Fehler sammeln -------------------------------------------------------------------------------------------------
  function own(st) { return typeof st === 'string' && /\/static\/clientlog\.js/.test(st); }
  function add(kind, msg, stack, src, line, col) {
    if (busy || off) return;
    busy = true;
    try {
      if (own(stack) || /\/static\/clientlog\.js/.test(String(src || ''))) return;   // eigener Fehler: nie melden
      var foreign = msg === 'Script error.' || msg === 'Script error';               // fremde Quelle: nur zaehlen
      var it = { k: kind, msg: foreign ? 'Script error.' : scrub(msg), screen: screen(), t: now() };
      if (!foreign) {
        var s = stackOf(stack); if (s) it.stack = s;
        var sr = srcOf(src); if (sr) it.src = sr;
        if (line > 0 && line < 1e7) it.line = line | 0;
        if (col > 0 && col < 1e8) it.col = col | 0;
      }
      var key = [it.k, it.msg, it.src || '', it.line || 0, it.col || 0].join('|');
      ring.push({ t: it.t, k: 'err', kind: it.k, msg: it.msg, src: it.src, line: it.line, col: it.col, screen: it.screen });
      if (ring.length > RING) ring.splice(0, ring.length - RING);
      if (byKey[key]) { byKey[key].n = (byKey[key].n || 1) + 1; }
      else {
        if (pending.length >= ITEMS) return;
        byKey[key] = it; pending.push(it);
      }
      schedule();
    } catch (e) { /* Logger-Fehler verschlucken */ } finally { busy = false; }
  }
  window.addEventListener('error', function (e) {
    if (!e || !(e instanceof ErrorEvent)) return;                 // Lade-Fehler von Bildern/Skripten: kein Programmfehler
    var er = e.error, st = '';
    try { st = er && typeof er.stack === 'string' ? er.stack : ''; } catch (x) { st = ''; }
    add('error', e.message || 'Error', st, e.filename, e.lineno, e.colno);
  });
  window.addEventListener('unhandledrejection', function (e) {
    var r = e ? e.reason : null, msg = 'unhandled rejection', st = '';
    try {
      if (r instanceof Error) { msg = (r.name || 'Error') + ': ' + (r.message || ''); st = typeof r.stack === 'string' ? r.stack : ''; }
      else if (r && typeof r === 'object' && typeof r.message === 'string') msg = r.message;
      else if (typeof r === 'string') msg = r;
      else if (r == null) msg = 'unhandled rejection';
      else msg = 'unhandled rejection (' + typeof r + ')';
    } catch (x) { return; }                                      // Getter wirft: nichts melden, keine Schleife
    add('unhandled', msg, st);
  });

  // --- Versand -------------------------------------------------------------------------------------------------------
  function osOf() {
    var u = navigator.userAgent || '', os = /Android/.test(u) ? 'Android' : /iPhone|iPad|iPod/.test(u) ? 'iOS' : /Mac OS X/.test(u) ? 'macOS'
      : /Windows/.test(u) ? 'Windows' : /CrOS/.test(u) ? 'ChromeOS' : /Linux/.test(u) ? 'Linux' : 'other';
    var b = /Edg\/(\d+)/.exec(u) ? 'Edge ' + /Edg\/(\d+)/.exec(u)[1] : /Firefox\/(\d+)/.exec(u) ? 'Firefox ' + /Firefox\/(\d+)/.exec(u)[1]
      : /Chrome\/(\d+)/.exec(u) ? 'Chrome ' + /Chrome\/(\d+)/.exec(u)[1] : /Version\/(\d+).*Safari/.exec(u) ? 'Safari ' + /Version\/(\d+).*Safari/.exec(u)[1] : '';
    if (/(Saga|Hlidskjalf)App\//.test(u)) b = (b ? b + ' ' : '') + 'app';
    return (os + (b ? '; ' + b : '')).slice(0, 60);
  }
  function devOf() {
    try {
      var sc = window.screen || {}, w = Math.min(sc.width || 0, sc.height || 0), coarse = matchMedia('(pointer: coarse)').matches;
      return coarse ? (w >= 700 ? 'tablet' : 'phone') : 'desktop';
    } catch (e) { return 'desktop'; }
  }
  var H = { 'X-Saga': '1', 'Content-Type': 'application/json' };
  function feature() {
    if (feat) return feat;
    if (!oFetch) { feat = Promise.resolve(false); return feat; }
    feat = oFetch('/api/version', { credentials: 'same-origin', headers: { 'X-Saga': '1' } }).then(function (r) { return r.json(); }).then(function (v) {
      ver = String(v.version || '').replace(/[^A-Za-z0-9._+\-]/g, '').slice(0, 40);
      return (v.features || []).indexOf('clientlog1') >= 0;
    }).catch(function () { feat = null; return false; });
    return feat;
  }
  function head() { return { v: 1, app: 'web', ver: ver, os: osOf(), dev: devOf(), sid: SID }; }
  function schedule() {
    if (timer || off || !pending.length) return;
    var wait = Math.max(1500, lastSent + RATE - Date.now());     // kurz sammeln (Fehler + Folgefehler in einem Bericht)
    timer = setTimeout(function () { timer = null; flush(false); }, wait);
  }
  function flush(leaving) {
    if (off || !pending.length || !oFetch) return;
    if (sent >= MAX_REPORTS) { off = true; return; }
    if (leaving && feat === null) return;                          // Feature unbekannt: beim Verlassen nicht erst fragen
    feature().then(function (on) {
      if (!on) { if (feat) { off = true; pending = []; byKey = {}; } return; }
      if (!pending.length) return;
      var items = pending; pending = []; byKey = {};
      var body = head(); body.items = items;
      var data = JSON.stringify(body);
      while (data.length > 15000 && body.items.length > 1) { body.items.pop(); data = JSON.stringify(body); }
      lastSent = Date.now(); sent++;
      oFetch('/api/client/log', { method: 'POST', credentials: 'same-origin', headers: H, body: data, keepalive: true }).then(function (r) {
        if (r.status === 404) off = true;                            // Schalter aus
        else if (r.status === 429) lastSent = Date.now() + 60000;    // Server bremst: laenger warten
      }).catch(function () {});
      schedule();
    }).catch(function () {});
  }
  function onLeave() { try { if (pending.length) flush(true); } catch (e) { /* egal */ } }
  window.addEventListener('pagehide', onLeave);
  document.addEventListener('visibilitychange', function () {
    ev('life', { st: document.hidden ? 'hidden' : 'visible' });
    if (document.hidden) onLeave();
  });
  window.addEventListener('online', function () { ev('life', { st: 'online' }); });
  window.addEventListener('offline', function () { ev('life', { st: 'offline' }); });
  window.addEventListener('hashchange', viewCheck);
  window.addEventListener('popstate', viewCheck);
  setInterval(function () { if (!document.hidden) viewCheck(); }, 3000);

  // --- Netzwerkstatus je Endpunkt-Muster (fetch) und Speichern von Seiten ---------------------------------------------
  if (oFetch) {
    window.fetch = function (input, init) {
      var p = oFetch.apply(window, arguments);
      try {
        var url = typeof input === 'string' ? input : input && input.url, path = pathOf(url);
        if (!path || !/^\/(api|app|fed)\//.test(path) || /^\/api\/client\//.test(path)) return p;
        var m = String((init && init.method) || (input && typeof input === 'object' && input.method) || 'GET').toUpperCase(), t0 = Date.now();
        if (['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'].indexOf(m) < 0) return p;
        var isSave = m === 'PATCH' && /^\/api\/notes\/:id$/.test(path);
        p.then(function (r) {
          ev('net', { m: m, p: path, s: r.status, ms: Date.now() - t0 });
          if (isSave) ev('save', r.ok ? { st: 'ok' } : r.status === 409 ? { st: 'conflict', s: 409 } : { st: 'error', s: r.status });
        }, function () {
          ev('net', { m: m, p: path, s: 0, ms: Date.now() - t0 });
          if (isSave) ev('save', { st: navigator.onLine === false ? 'offline' : 'error', s: 0 });
        });
      } catch (e) { /* Aufrufer bekommt immer das Original */ }
      return p;
    };
  }
  // WebSocket (Zusammenarbeit /api/collab, Anruf-Knoten): nur der Zustand, nie die Adresse
  if (window.WebSocket) {
    var OWS = window.WebSocket;
    var WS = function (u, pr) {
      var ws = pr === undefined ? new OWS(u) : new OWS(u, pr);
      try {
        var ch = /\/api\/collab/.test(String(u)) ? 'collab' : /\/api\/push/.test(String(u)) ? 'push' : /\/api\/sfu\//.test(String(u)) ? 'call' : 'ws';
        ws.addEventListener('open', function () { ev('conn', { ch: ch, st: 'open' }); });
        ws.addEventListener('error', function () { ev('conn', { ch: ch, st: 'error' }); });
        ws.addEventListener('close', function () { ev('conn', { ch: ch, st: 'closed' }); });
      } catch (e) { /* egal */ }
      return ws;
    };
    WS.prototype = OWS.prototype;
    ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (k) { try { Object.defineProperty(WS, k, { value: OWS[k] }); } catch (e) { /* egal */ } });
    window.WebSocket = WS;
  }

  // --- "Diagnose senden" ------------------------------------------------------------------------------------------------
  function diag(note) {
    return feature().then(function (on) {
      if (!on) { var e = new Error('off'); e.status = 404; throw e; }
      var body = head(), evs = ring.slice(-RING).map(function (x) {
        var y = {}; for (var k in x) if (x[k] !== undefined && x[k] !== '') y[k] = x[k];
        return y;
      });
      body.ev = evs;
      var n = String(note || '').trim().slice(0, 1000); if (n) body.note = n;
      var data = JSON.stringify(body);
      while (data.length > 60000 && body.ev.length) { body.ev.shift(); data = JSON.stringify(body); }
      return oFetch('/api/client/diag', { method: 'POST', credentials: 'same-origin', headers: H, body: data }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) { var e = new Error(d.error || ('HTTP ' + r.status)); e.status = r.status; throw e; }
          return d;
        });
      });
    });
  }

  ev('life', { st: 'start' });
  viewCheck();
  window.HKLog = {
    ev: function (k, d) { ev(k, d); },
    report: function (err, kind) { try { add(kind === 'warn' ? 'warn' : 'error', err && err.message ? (err.name || 'Error') + ': ' + err.message : String(err), err && err.stack); } catch (e) { /* egal */ } },
    diag: diag,
    enabled: function () { return feature(); },
    // nur fuer Tests/Entwicklung: Kopie des Puffers bzw. der wartenden Eintraege
    _ring: function () { return ring.slice(); },
    _pending: function () { return pending.slice(); },
    scrub: scrub
  };
})();
