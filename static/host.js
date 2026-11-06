/* Saga - Brueckenvertrag zur Desktop-Huelle (2.5, P1; Vertrag docs/APP-API-HOST.md). Laedt direkt nach common.js.
   window.sagaHost === HK.host. Ohne Huelle (normaler Browser) sind alle Aufrufe No-ops bzw. fallen auf das heutige
   Web-Verhalten zurueck (openExternal -> neues Fenster; Mitteilungen weiter ueber Web-Push/Service Worker).
   Erkennung: User-Agent "SagaApp/<version>" (aeltere Apps "HlidskjalfApp/<version>") UND ein Nachrichtenweg:
   - macOS (WKWebView):   window.webkit.messageHandlers.hk.postMessage(msg)
   - Tauri 2 (Win/Linux): window.__TAURI_INTERNALS__.invoke('saga_host', { msg: msg })
   Die Huelle meldet Art und Faehigkeiten vor dem Laden (window.sagaHostInfo = {kind, version, caps}) oder spaeter per
   sagaHost.receive({type:'hello', ...}). Ohne Meldung (alte native App mit Web-Ansicht): kind 'app', caps settings+token.
   Das Web schickt nur, was gemeldet ist. Nachrichten gehen nur aus dem Hauptfenster der eigenen Origin (iframes
   derselben Origin reichen an das Hauptfenster weiter); Pfade von der Huelle muessen relativ zur eigenen Origin sein. */
(function () {
  'use strict';
  var HK = window.HK; if (!HK || HK.host) return;
  var CAPS = ['badge', 'notify', 'widgets', 'openExternal', 'settings', 'token'];
  var UA = /\b(?:Saga|Hlidskjalf)App\/([\w.+-]{1,40})/.exec(navigator.userAgent || '');
  var TOP = window.top === window;

  function wkHandler() {
    try { var h = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.hk; return h && typeof h.postMessage === 'function' ? h : null; }
    catch (e) { return null; }
  }
  function tauri() {
    try { var t = window.__TAURI_INTERNALS__; return t && typeof t.invoke === 'function' ? t : null; } catch (e) { return null; }
  }
  // Nachrichtenweg passend zur Art; null = keine Huelle
  function transport(kind) {
    if (!UA || !TOP) return null;
    if (kind === 'tauri') { var t = tauri(); return t ? function (m) { return t.invoke('saga_host', { msg: m }); } : null; }
    var h = wkHandler();
    return h ? function (m) { h.postMessage(m); } : null;
  }
  function cleanCaps(a) { return Array.isArray(a) ? a.filter(function (c, i) { return CAPS.indexOf(c) >= 0 && a.indexOf(c) === i; }) : []; }

  var host = { v: 1, kind: null, version: null, caps: [] }, send = null, handlers = {};
  function setup(info) {
    var kind = info && (info.kind === 'mac' || info.kind === 'tauri') ? info.kind : null;
    if (!kind && tauri() && !wkHandler()) kind = 'tauri';
    var tr = transport(kind || 'mac');
    if (!tr) return false;
    send = tr;
    if (kind) { host.kind = kind; host.caps = cleanCaps(info && info.caps); }
    else { host.kind = 'app'; host.caps = ['settings', 'token']; }       // alte native App (iOS/macOS) mit Web-Ansicht
    host.version = String((info && info.version) || UA[1]).slice(0, 40);
    return true;
  }
  var pre = null; try { pre = window.sagaHostInfo; } catch (e) { pre = null; }
  setup(pre && typeof pre === 'object' ? pre : null);

  // im iframe (Peek, Chat-Panel): an das Hauptfenster derselben Origin weiterreichen
  function upper() {
    if (TOP) return null;
    try { return window.top.HK && window.top.HK.host && window.top.HK.host !== host ? window.top.HK.host : null; } catch (e) { return null; }
  }
  function has(cap) { var u = upper(); return u ? u.has(cap) : !!send && host.caps.indexOf(cap) >= 0; }
  function post(msg) {
    var u = upper(); if (u) return u.post(msg);
    if (!send || !msg || typeof msg.action !== 'string') return false;
    try { var r = send(msg); if (r && typeof r.catch === 'function') r.catch(function () {}); return true; } catch (e) { return false; }
  }
  function str(s, n) { return String(s == null ? '' : s).replace(/[\u0000-\u001f\u007f]+/g, ' ').trim().slice(0, n); }
  function ownPath(u) {                               // nur Pfade der eigenen Origin ("/..." bzw. volle Adresse derselben Origin)
    if (typeof u !== 'string' || !u) return null;
    try { var x = new URL(u, location.origin); return x.origin === location.origin ? x.pathname + x.search + x.hash : null; } catch (e) { return null; }
  }
  function foreign(u) {                                // fremder Web-/Mail-Link (sonst null)
    try {
      var x = new URL(String(u), location.href);
      if (x.protocol === 'mailto:') return x.href;
      return (x.protocol === 'http:' || x.protocol === 'https:') && x.origin !== location.origin ? x.href : null;
    } catch (e) { return null; }
  }

  // --- Web -> Huelle ------------------------------------------------------------------------------------------------
  var lastBadge = null, wantBadge = null;             // gesendet / zuletzt gewuenscht (fuer eine spaete Meldung)
  host.badge = function (n) {
    n = Math.max(0, Math.min(99999, parseInt(n, 10) || 0));
    wantBadge = n;
    if (!has('badge')) return false;
    if (n === lastBadge && !upper()) return true;
    lastBadge = n;
    return post({ action: 'badge', v: 1, n: n });
  };
  host.notify = function (title, body, url, o) {
    if (!has('notify')) return false;                  // sonst: bisherige Logik (Web-Push ueber den Service Worker)
    o = o || {};
    var m = { action: 'notify', v: 1, title: str(title, 120) || 'Saga', body: str(body, 300), url: ownPath(url) || '/' };
    if (o.tag) m.tag = str(o.tag, 64);
    if (o.kind) m.kind = str(o.kind, 40);
    if (o.id != null) m.id = parseInt(o.id, 10) || 0;
    return post(m);
  };
  // Widgets neu laden: hoechstens alle 10 s (erster Aufruf sofort, weitere am Ende des Fensters zusammengefasst)
  var W_MS = 10000, wLast = 0, wT = null;
  host.widgetsReload = function () {
    if (!has('widgets')) return false;
    var u = upper(); if (u) return u.widgetsReload();
    var wait = wLast + W_MS - Date.now();
    if (wait <= 0) { wLast = Date.now(); return post({ action: 'widgets.reload', v: 1 }); }
    if (!wT) wT = setTimeout(function () { wT = null; wLast = Date.now(); post({ action: 'widgets.reload', v: 1 }); }, wait);
    return true;
  };
  host.openExternal = function (url) {
    var f = foreign(url);
    if (!f) return false;
    if (has('openExternal')) return post({ action: 'openExternal', v: 1, url: f.slice(0, 4000) });
    nativeOpen(f, '_blank', 'noopener');             // ohne Huelle: wie bisher ein neues Fenster
    return false;
  };
  host.has = has;
  host.post = post;

  // --- Huelle -> Web ------------------------------------------------------------------------------------------------
  host.on = function (ev, fn) {
    if (typeof fn !== 'function') return function () {};
    (handlers[ev] = handlers[ev] || []).push(fn);
    return function () { var a = handlers[ev] || [], i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); };
  };
  function emit(ev, d) {
    var out = true;
    (handlers[ev] || []).slice().forEach(function (fn) { try { if (fn(d) === false) out = false; } catch (e) { /* naechster */ } });
    return out;
  }
  // Deep-Link der Huelle (saga://..., hlidskjalf://...) -> Web-Adresse dieser Origin; null = nicht fuer das Web
  // (auth, pair, Unbekanntes mit Daten). Tabelle in docs/APP-API-HOST.md.
  var SET = /^[a-z]{2,20}$/;
  host.route = function (link) {
    var m = /^(saga|hlidskjalf):\/\/([a-z-]*)(\/[^?#]*)?(\?[^#]*)?/i.exec(String(link || '').trim());
    if (!m) return null;
    var h = m[2].toLowerCase(), arg = (m[3] || '').replace(/^\/+|\/+$/g, ''), q = new URLSearchParams(m[4] || '');
    var id = /^\d{1,12}$/.test(arg) ? arg : null;
    switch (h) {
      case '': case 'today': return '/';
      case 'page': return id ? '/notes#' + id : null;
      case 'tasks': return '/notes?v=tasks';
      case 'events': return '/notes?v=events';
      case 'chats': return '/chats';
      case 'new-task': return '/notes?v=tasks&new=task';
      case 'new-page': return '/notes?new=blank';
      case 'new':                                       // anlegen nur nativ (Huelle prueft k=…); das Web zeigt das Ziel
        var p = q.get('db') || q.get('parent');
        return p && /^\d{1,12}$/.test(p) ? '/notes#' + p : '/notes?new=blank';
      case 'settings': return '/notes' + (SET.test(arg) ? '?settings=' + arg : '?settings=account');
      default: return null;                             // auth, pair: nur in der Huelle
    }
  };
  function go(path) {
    path = ownPath(path); if (!path) return false;
    var s = /^\/notes\?settings=([a-z]+)$/.exec(path);
    if (s && window.HKSettings) { window.HKSettings.open(s[1]); return true; }     // Einstellungen ohne Neuladen
    var here = location.pathname + location.search;
    if (path.indexOf('#') > 0 && path.split('#')[0] === here) { location.hash = path.split('#')[1]; return true; }
    location.assign(path);
    return true;
  }
  host.open = function (linkOrPath) {
    var p = /^(saga|hlidskjalf):/i.test(String(linkOrPath || '')) ? host.route(linkOrPath) : linkOrPath;
    return p ? go(p) : false;
  };
  // Aufruf der Huelle (evaluateJavaScript / webview.eval): sagaHost.receive({type: ..., ...}) oder als JSON-Text
  host.receive = function (evt) {
    if (!TOP) return false;
    if (typeof evt === 'string') { try { evt = JSON.parse(evt); } catch (e) { return false; } }
    if (!evt || typeof evt !== 'object' || typeof evt.type !== 'string') return false;
    var t = evt.type;
    if (t === 'hello') {
      if (!setup(evt)) return false;
      emit('hello', { kind: host.kind, caps: host.caps.slice() });
      if (wantBadge != null) { lastBadge = null; host.badge(wantBadge); }
      return true;
    }
    if (!send) return false;                            // ohne Huelle keine Ereignisse annehmen
    if (t === 'open') {                                 // Deep-Link oder Pfad; Handler kann mit false selbst uebernehmen
      if (emit('open', evt) === false) return true;
      return host.open(evt.link || evt.path);
    }
    if (t === 'notificationClick') { emit(t, evt); return go(evt.url || '/'); }
    if (t === 'active' || t === 'online' || t === 'offline' || t === 'widgets.done') return emit(t, evt), true;
    emit(t, evt);
    return true;
  };

  // --- Anbindung ans Web ----------------------------------------------------------------------------------------------
  // Aenderungen, die Widgets zeigen (Aufgaben, Termine, Seiten/Eintraege, Papierkorb): nach erfolgreichem Schreiben
  // (HK.api in common.js meldet jede Nicht-GET-Anfrage hierher)
  var WRITE = /^\/api\/(tasks|calendar\/events|trash|gallery\/\d+\/use|import\/[^/]+\/run|notes)(\/|\?|$)/;
  var NOT = /^\/api\/notes\/(search|[^/]+\/(comments|formula|widgets\/preview|versions\/checkpoint|discard|publish|shares))/;
  host._changed = function (method, url) {
    if (method === 'GET' || !has('widgets')) return;
    var p = ownPath(url); if (!p) return;
    p = p.split('#')[0];
    if (WRITE.test(p) && !NOT.test(p) && !/^\/api\/tasks\/digest/.test(p)) host.widgetsReload();
  };
  // Seiteninhalt geht im Live-Modus (notes.live) ueber /api/collab statt HK.api: nach Eingaben beim Seitenwechsel und
  // beim Wechsel in den Hintergrund anstossen
  var typed = false;
  document.addEventListener('input', function () { typed = true; }, true);
  function flushTyped() { if (typed && has('widgets')) { typed = false; host.widgetsReload(); } }
  document.addEventListener('hk-page-open', flushTyped);
  document.addEventListener('visibilitychange', function () { if (document.hidden) flushTyped(); });

  // Externe Links: fremde http(s)/mailto-Links und window.open zu fremden Adressen ueber die Huelle (Systembrowser)
  var nativeOpen = window.open.bind(window);
  // Immer einrichten, Faehigkeit erst beim Klick pruefen (die Huelle darf sich spaet per 'hello' melden); ohne Huelle Durchreichen
  {
    document.addEventListener('click', function (e) {
      if (!has('openExternal') || e.defaultPrevented || e.button !== 0) return;
      var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
      // Links im Editor: normaler Klick setzt den Cursor (Strg/Cmd-Klick geht ueber window.open)
      if (!a || a.hasAttribute('download') || a.isContentEditable || a.closest('[contenteditable=""],[contenteditable="true"]')) return;
      var f = foreign(a.getAttribute('href'));
      if (f && host.openExternal(f)) e.preventDefault();
    });
    window.open = function (u) {
      var f = u && has('openExternal') ? foreign(u) : null;
      if (f && host.openExternal(f)) return null;
      return nativeOpen.apply(null, arguments);
    };
  }

  if (host.kind === 'mac' || host.kind === 'tauri') {
    var ready = function () { post({ action: 'ready', v: 1, path: location.pathname + location.search + location.hash }); };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready); else ready();
  }
  HK.host = host;
  try { Object.defineProperty(window, 'sagaHost', { value: host, configurable: false, writable: false }); } catch (e) { window.sagaHost = host; }
})();
