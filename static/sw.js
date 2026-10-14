// Saga - Service Worker fuer Mitteilungen (Web Push, 1.7). Ausgeliefert unter /sw.js (Geltungsbereich "/").
// Nur push / notificationclick / pushsubscriptionchange - KEIN fetch-Handler (Saga bleibt eine normale Webseite).
// Inhalt kommt Ende-zu-Ende verschluesselt (RFC 8291) vom Saga-Server: {title, body, url, tag, kind, ts}.
'use strict';

self.addEventListener('install', function () { self.skipWaiting(); });
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
