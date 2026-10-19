// Saga - Service Worker (Geltungsbereich "/"), ausgeliefert unter /sw.js:
// - Mitteilungen (Web Push, 1.7): push / notificationclick / pushsubscriptionchange. Inhalt kommt Ende-zu-Ende verschluesselt
//   (RFC 8291) vom Saga-Server: {title, body, url, tag, kind, ts}.
// - Videos im Chat (2.4): Wiedergabe und Speichern von Anhaengen im Format saga-file/v2 (saga/file_v2.py). Die Seite meldet
//   eine Wiedergabe mit postMessage {op:'media', id, fid, key, size, cs, m, token, name} an; <video src="/_m/<id>"> fragt
//   dann hier mit Range (Klartext-Bytes). Der Service Worker holt nur die noetigen Ciphertext-Bloecke (Range, Kopf
//   X-Saga-Media-Token), entschluesselt sie blockweise und antwortet mit 206 als Strom - kein Komplett-Download, kein
//   Klartext im Cache-Speicher. Schluessel nur im Speicher (Map), Kennung je Wiedergabe zufaellig (128 Bit), nur eigene
//   Origin; "clear" beim Abmelden. Der fetch-Handler kuemmert sich nur um /_m/ - alles andere geht unveraendert ans Netz
//   (in Chromium per Static Routing, ohne den Service Worker zu starten).
'use strict';
var V = '__SAGA_V__';

self.addEventListener('install', function (e) {
  self.skipWaiting();
  // Static Routing (Chromium 123+): nur /_m/ geht an den fetch-Handler, alles andere direkt ans Netz
  if (e.addRoutes) {
    try {
      e.waitUntil(e.addRoutes([{ condition: { not: { urlPattern: new URLPattern({ pathname: '/_m/*' }) } }, source: 'network' }])
        .catch(function () {}));
    } catch (x) { /* aeltere Fassung der Schnittstelle: dann eben ueber den fetch-Handler */ }
  }
  try { libLoad(); } catch (x) { /* beim ersten Video */ }   // Bibliothek beim Installieren ablegen (importScripts spaeter)
});
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

function sameOrigin(url) {
  try { var u = new URL(url || '/', self.location.origin); return u.origin === self.location.origin ? u.href : self.location.origin + '/'; }
  catch (x) { return self.location.origin + '/'; }
}

self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {}; }
  var title = String(d.title || 'Saga').slice(0, 120);
  var opts = {
    body: String(d.body || '').slice(0, 300),
    tag: d.tag ? String(d.tag).slice(0, 64) : undefined,
    renotify: !!d.tag,
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    timestamp: d.ts ? d.ts * 1000 : Date.now(),
    data: { url: sameOrigin(d.url) }
  };
  if (d.kind === 'call') {                            // Anruf (2.1): bleibt stehen, bis man reagiert; vibriert wie ein Anruf
    opts.requireInteraction = true; opts.vibrate = [400, 200, 400, 200, 400];
  }
  e.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || self.location.origin + '/';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (c.url === url && 'focus' in c) return c.focus();
    }
    for (var j = 0; j < list.length; j++) {          // offenes Saga-Fenster wiederverwenden
      if ('navigate' in list[j] && 'focus' in list[j]) return list[j].focus().then(function (w) { return w.navigate(url); });
    }
    return self.clients.openWindow(url);
  }));
});

// Browser hat das Abo erneuert (z. B. abgelaufen): neues Abo beim Server anmelden. Die Sitzung reist als Cookie mit;
// ohne Sitzung schlaegt es fehl - dann meldet sich der Browser beim naechsten Oeffnen von Saga neu an (static/push.js).
self.addEventListener('pushsubscriptionchange', function (e) {
  var opts = e.oldSubscription && e.oldSubscription.options;
  if (!opts || !opts.applicationServerKey) return;
  e.waitUntil(self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: opts.applicationServerKey })
    .then(function (sub) {
      var j = sub.toJSON();
      return fetch('/api/push/subscriptions', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-Saga': '1' },
        body: JSON.stringify({ type: 'webpush', endpoint: j.endpoint, keys: j.keys, name: '' })
      });
    }).catch(function () {}));
});

// --- Videos im Chat (2.4): Wiedergabe/Speichern von saga-file/v2 ---------------------------------------------------------
var M = new Map();                                   // Kennung -> Wiedergabe {fid, key, size, cs, m, token, name, info}
var X = null, libP = null;
var HDR = 48, TAG = 16, GROUP = 4;                   // je Abruf hoechstens 4 Bloecke (1 MiB)
var MIME = /^(video|audio)\/[\w.+-]{1,60}$/;

function libLoad() {
  if (!libP) {
    importScripts('/static/vendor/libsodium/libsodium-sumo.js?v=' + V, '/static/vendor/libsodium/libsodium-wrappers.js?v=' + V,
      '/static/chat-crypto.js?v=' + V);
    libP = self.sodium.ready.then(function () { X = self.SagaChatCrypto(self.sodium); return X; });
  }
  return libP;
}

self.addEventListener('message', function (e) {
  var d = e.data || {}, port = e.ports && e.ports[0];
  if (!e.source || (e.origin && e.origin !== self.location.origin)) return;
  if (d.op === 'media' && typeof d.id === 'string' && /^[0-9a-f]{32}$/.test(d.id)) {
    if (M.size > 50) M.delete(M.keys().next().value);           // nur die letzten Wiedergaben
    M.set(d.id, { fid: String(d.fid), key: d.key, size: +d.size, cs: +d.cs || 262144, m: MIME.test(d.m || '') ? d.m : 'application/octet-stream',
                  token: String(d.token || ''), name: String(d.name || 'video').slice(0, 200), info: null, at: Date.now() });
    if (port) port.postMessage({ ok: true });
  } else if (d.op === 'drop') {
    M.delete(d.id);
  } else if (d.op === 'clear') {                                // Abmelden, "Neu beginnen", Geraet entfernt
    M.clear();
    if (port) port.postMessage({ ok: true });
  }
});

self.addEventListener('fetch', function (e) {
  var u;
  try { u = new URL(e.request.url); } catch (x) { return; }
  if (u.origin !== self.location.origin || u.pathname.indexOf('/_m/') !== 0 || e.request.method !== 'GET') return;
  e.respondWith(media(e, u));
});

// Kennung unbekannt (Service Worker wurde zwischendurch beendet): die Seiten fragen, die die Wiedergabe angemeldet haben
function ask(id) {
  return self.clients.matchAll({ type: 'window' }).then(function (list) {
    return new Promise(function (res) {
      var left = list.length, done = false;
      if (!left) return res(null);
      var t = setTimeout(function () { if (!done) { done = true; res(null); } }, 2500);
      list.forEach(function (c) {
        var ch = new MessageChannel();
        ch.port1.onmessage = function (ev) {
          var d = ev.data;
          if (!done && d && d.op === 'media' && d.id === id) {
            done = true; clearTimeout(t);
            M.set(id, { fid: String(d.fid), key: d.key, size: +d.size, cs: +d.cs || 262144, m: MIME.test(d.m || '') ? d.m : 'application/octet-stream',
                        token: String(d.token || ''), name: String(d.name || 'video').slice(0, 200), info: null, at: Date.now() });
            res(M.get(id));
          } else if (--left <= 0 && !done) { done = true; clearTimeout(t); res(null); }
        };
        c.postMessage({ op: 'saga-media-need', id: id }, [ch.port2]);
      });
    });
  });
}

function token(ent) {                                // neues Medien-Token mit der Sitzung (Cookie) holen
  return fetch('/api/chat/files/' + encodeURIComponent(ent.fid) + '/token', { method: 'POST', credentials: 'same-origin', headers: { 'X-Saga': '1' } })
    .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(function (d) { ent.token = d.token; return ent; });
}

// Ciphertext-Bytes from..to (einschliesslich) holen; Token abgelaufen -> einmal erneuern
function cget(ent, from, to, signal, again) {
  return fetch('/api/chat/files/' + encodeURIComponent(ent.fid), { credentials: 'omit', cache: 'no-store', signal: signal,
                                                               headers: { 'Range': 'bytes=' + from + '-' + to, 'X-Saga-Media-Token': ent.token } })
    .then(function (r) {
      if ((r.status === 401 || r.status === 403) && !again) return token(ent).then(function () { return cget(ent, from, to, signal, true); });
      if (r.status !== 206) throw new Error('HTTP ' + r.status);
      return r.arrayBuffer();
    }).then(function (b) {
      if (b.byteLength !== to - from + 1) throw new Error('length');
      return new Uint8Array(b);
    });
}

function head(ent) {
  if (ent.info) return Promise.resolve(ent.info);
  return cget(ent, 0, HDR - 1).then(function (h) {
    var info = X.f2Parse(h);
    if (info.size !== ent.size || info.cs !== ent.cs) throw new Error('header');
    ent.info = info; ent.K = X.unb64(ent.key, 32);
    return info;
  });
}

function bad(code) { return new Response(null, { status: code, headers: { 'Cache-Control': 'no-store' } }); }

function media(e, u) {
  var id = u.pathname.slice(4), dl = u.searchParams.get('dl') === '1';
  return libLoad().then(function () {
    var ent = M.get(id);
    return ent ? ent : ask(id);
  }).then(function (ent) {
    if (!ent) return bad(404);
    return head(ent).then(function (info) {
      var size = info.size, a = 0, b = size - 1, status = 200;
      var rh = e.request.headers.get('range');
      var m = rh && !dl ? /^bytes=(\d*)-(\d*)/.exec(rh.trim()) : null;
      if (m && (m[1] || m[2])) {
        if (!m[1]) { var n = +m[2]; a = Math.max(0, size - n); }
        else { a = +m[1]; if (m[2]) b = Math.min(+m[2], size - 1); }
        if (a >= size || b < a) return new Response(null, { status: 416, headers: { 'Content-Range': 'bytes */' + size, 'Cache-Control': 'no-store' } });
        status = 206;
      }
      var cs = info.cs, pos = a, ac = new AbortController();
      var stream = new ReadableStream({
        pull: function (ctl) {
          if (pos > b) { ctl.close(); return; }
          var i = Math.floor(pos / cs), j = Math.min(Math.floor(b / cs), i + GROUP - 1), r = X.f2Range(size, i * cs, Math.min(size - 1, (j + 1) * cs - 1), cs);
          return cget(ent, r.from, r.to, ac.signal).then(function (ct) {
            for (var k = i; k <= j && pos <= b; k++) {
              var off = X.f2Off(k, cs) - r.from, blk = X.f2Open(ent.K, info, k, ct.subarray(off, Math.min(off + cs + TAG, ct.length)));
              var s0 = pos - k * cs, s1 = Math.min(blk.length, b - k * cs + 1);
              if (s1 > s0) ctl.enqueue(blk.subarray(s0, s1));
              pos = k * cs + s1;
            }
          });
        },
        cancel: function () { ac.abort(); }
      }, { highWaterMark: 0 });
      var h = { 'Content-Type': dl ? 'application/octet-stream' : ent.m, 'Content-Length': String(b - a + 1), 'Accept-Ranges': 'bytes',
                'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' };
      if (status === 206) h['Content-Range'] = 'bytes ' + a + '-' + b + '/' + size;
      if (dl) h['Content-Disposition'] = "attachment; filename*=UTF-8''" + encodeURIComponent(ent.name);
      return new Response(stream, { status: status, headers: h });
    });
  }).catch(function () { return bad(502); });
}
