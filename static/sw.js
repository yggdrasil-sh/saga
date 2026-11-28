// Saga - Service Worker (Geltungsbereich "/"), ausgeliefert unter /sw.js:
// - Mitteilungen (Web Push, 1.7): push / notificationclick / pushsubscriptionchange. Inhalt kommt Ende-zu-Ende verschluesselt
//   (RFC 8291) vom Saga-Server: {title, body, url, tag, kind, ts}. 2.6 (2270): Auftraege der Kollegin (Nutzlast 3) mit
//   Knoepfen "Spaeter"/"Gerade nicht"/"Ansehen" (jobNote/jobAct unten).
// - Videos im Chat (2.4): Wiedergabe und Speichern von Anhaengen im Format saga-file/v2 (saga/file_v2.py). Die Seite meldet
//   eine Wiedergabe mit postMessage {op:'media', id, fid, key, size, cs, m, token, name} an; <video src="/_m/<id>"> fragt
//   dann hier mit Range (Klartext-Bytes). Der Service Worker holt nur die noetigen Ciphertext-Bloecke (Range, Kopf
//   X-Saga-Media-Token), entschluesselt sie blockweise und antwortet mit 206 als Strom - kein Komplett-Download, kein
//   Klartext im Cache-Speicher. Schluessel nur im Speicher (Map), Kennung je Wiedergabe zufaellig (128 Bit), nur eigene
//   Origin; "clear" beim Abmelden. Der fetch-Handler kuemmert sich um /_m/ und (fuer die Offline-Seite) um
//   GET-Navigationen - alles andere geht unveraendert ans Netz (in Chromium per Static Routing, ohne den Service Worker zu starten).
// - Teilen-Ziel der installierten PWA (2.6, Bugs-DB 2275 K-G, APP-API-KI 14.5): das Manifest schickt geteilte Inhalte
//   per POST multipart/form-data an /share. Der Service Worker liest title/text/url, legt sie EINMAL lesbar unter einem
//   zufaelligen Schluessel (128 Bit) im Cache "saga-share" ab (hoechstens 10 min, hoechstens 5 Eintraege) und antwortet
//   mit 303 /saga?share=<schluessel> - der Text erreicht weder eine Adresse noch das Zugriffs-Log des Servers.
//   static/saga.js holt den Eintrag ab und loescht ihn. Ohne Service Worker antwortet der Server mit 303 share_failed.
// - Offline-Seite (2.5, Desktop-Huelle O0): /static/offline.html liegt im Cache; schlaegt eine Seiten-Navigation
//   (GET, eigene Origin) im Netz fehl, zeigt der Service Worker sie an Stelle der Seite. HTTP-Fehler des Servers bleiben,
//   wie sie sind; andere Anfragen beruehrt das nicht.
'use strict';
var V = '__SAGA_V__';
var OFF = 'saga-offline-', OFF_URL = '/static/offline.html';
var SHARE = 'saga-share', SHARE_TTL = 10 * 60 * 1000, SHARE_MAX = 5;

self.addEventListener('install', function (e) {
  self.skipWaiting();
  // Static Routing (Chromium 123+): nur /_m/ und Seiten-Navigationen (Offline-Seite) gehen an den fetch-Handler, alles
  // andere direkt ans Netz. Kennt der Browser die Bedingung requestMode nicht, gilt die bisherige Regel (nur /_m/).
  if (e.addRoutes) {
    try {
      var net = { condition: { not: { urlPattern: new URLPattern({ pathname: '/_m/*' }) } }, source: 'network' };
      // 2275: /share (Teilen-Ziel, POST) immer an den fetch-Handler - vor der Netz-Regel, sonst sieht der Server den Inhalt
      var shr = { condition: { urlPattern: new URLPattern({ pathname: '/share' }) }, source: 'fetch-event' };
      e.waitUntil(e.addRoutes([shr, { condition: { requestMode: 'navigate', requestMethod: 'GET' }, source: 'fetch-event' }, net])
        .catch(function () { return e.addRoutes([shr, net]); }).catch(function () {}));
    } catch (x) { /* aeltere Fassung der Schnittstelle: dann eben ueber den fetch-Handler */ }
  }
  e.waitUntil(caches.open(OFF + V).then(function (c) { return c.add(new Request(OFF_URL, { cache: 'reload' })); }).catch(function () {}));
  try { libLoad(); } catch (x) { /* beim ersten Video */ }   // Bibliothek beim Installieren ablegen (importScripts spaeter)
});
self.addEventListener('activate', function (e) {
  // Navigation Preload: die Netzanfrage startet parallel zum Aufwachen des Service Workers (kein Zeitverlust je Navigation)
  var pre = self.registration.navigationPreload ? self.registration.navigationPreload.enable().catch(function () {}) : null;
  e.waitUntil(Promise.all([pre, self.clients.claim(), caches.keys().then(function (ks) {   // alte Fassungen der Offline-Seite weg
    return Promise.all(ks.filter(function (k) { return k.indexOf(OFF) === 0 && k !== OFF + V; }).map(function (k) { return caches.delete(k); }));
  }).catch(function () {})]));
});

function sameOrigin(url) {
  try { var u = new URL(url || '/', self.location.origin); return u.origin === self.location.origin ? u.href : self.location.origin + '/'; }
  catch (x) { return self.location.origin + '/'; }
}

// 2.6 (Bugs-DB 2270 K-C, Nutzlast 3 `jobpush1`): Mitteilungen zu Auftraegen der Kollegin (job.needs_you/done/failed).
// Titel/Text: mit detail (nur Ende-zu-Ende und Einstellung "Inhalt in Mitteilungen" != nie) t1/t2 vom Server, sonst der
// neutrale Satz der Nutzlast. Knoepfe nach K15 Geraet "web" (tests/vectors/push_kollegin.json k15): nur "Spaeter"/"Gerade
// nicht" (mit Einmal-Token) und "Ansehen" - nie "Passt, los"/"Rueckgaengig", auch wenn ein Token dafuer mitkaeme. Klick =
// Ansehen (Deep-Link url). Knopf -> POST /api/ai/jobs/{id}/act mit Sitzung + X-Saga; 409/410/403 (und Netzfehler nach
// einer Wiederholung) -> Auftrag im Fenster oeffnen. Woerter der Knoepfe/des Hinweises = texts der Vektoren.
var JOB_TX = {
  de: { later: 'Später', quiet: 'Gerade nicht', open: 'Ansehen', hint: 'Bitte in Saga ansehen' },
  en: { later: 'Later', quiet: 'Not now', open: 'View', hint: 'Please take a look in Saga' },
  es: { later: 'Más tarde', quiet: 'Ahora no', open: 'Ver', hint: 'Échale un vistazo en Saga' },
  ru: { later: 'Позже', quiet: 'Не сейчас', open: 'Открыть', hint: 'Посмотри в Saga' },
  th: { later: 'ภายหลัง', quiet: 'ไม่ใช่ตอนนี้', open: 'ดู', hint: 'โปรดดูใน Saga' }
};
var JOB_WEB = { needs: ['open', 'later'], done: ['quiet', 'open'], failed: ['open'] };   // K15, Geraet web (Reihenfolge)
var JOB_TOK = /^[A-Za-z0-9_-]{16,64}$/;
function isJob(kind) { return typeof kind === 'string' && /^job\.(needs_you|done|failed)$/.test(kind); }
function jobUrl(u) {                                   // nur /saga?tab=jobs[&job=<ziffern>] der eigenen Origin
  var s = String(u || ''), m = /^\/saga\?tab=jobs(&job=\d{1,18})?$/.exec(s);
  return self.location.origin + (m ? s : '/saga?tab=jobs');
}
function jobNote(d) {
  var det = d.detail && typeof d.detail === 'object' ? d.detail : null;
  var lang = String((det && det.lang) || d.lang || 'de'), tx = JOB_TX[lang] || JOB_TX[lang.slice(0, 2)] || JOB_TX.de;
  var withText = !!(det && typeof det.t1 === 'string' && det.t1);
  var title = withText ? det.t1 : String(d.title || 'Saga');
  var body = withText ? String(det.t2 || '') : String(d.body || '');
  var tok = {}, job = /^\d{1,18}$/.test(String(d.job)) ? +d.job : null;
  if (job != null && Array.isArray(d.actions)) d.actions.forEach(function (x) {
    if (x && (x.a === 'later' || x.a === 'quiet') && JOB_TOK.test(String(x.t || ''))) tok[x.a] = String(x.t);   // approve/undo nie im Web
  });
  var acts = (JOB_WEB[d.state] || ['open']).filter(function (a) { return a === 'open' || tok[a]; });
  var max = self.Notification && Notification.maxActions;
  if (typeof max === 'number' && max >= 0) acts = acts.slice(0, max);
  var opts = {
    body: body.slice(0, 300), tag: String(d.tag || 'saga-job').slice(0, 64), renotify: true,
    icon: '/static/icon-192.png', badge: '/static/icon-192.png', timestamp: d.ts ? d.ts * 1000 : Date.now(),
    actions: acts.map(function (a) { return { action: a, title: tx[a] }; }),
    data: { url: jobUrl(d.url), kind: d.kind, job: job, pv: typeof d.pv === 'number' ? d.pv : null, tok: tok, lang: lang }
  };
  if (d.silent) opts.silent = true;
  return { title: title.slice(0, 120), opts: opts };
}
// Knopf in der Mitteilung: act mit dem Einmal-Token (Sitzung reist als Cookie mit) -> 200 fertig (Mitteilung ist schon zu),
// 404 nichts mehr zu tun, sonst den Auftrag oeffnen (409 stale/state/changed/open, 410 used/expired/gone, 403 device/cap/key).
function jobAct(data, a) {
  var tok = data.tok && data.tok[a];
  if (!tok || data.job == null) return openUrl(data.url, data);
  var body = JSON.stringify({ action: a, atok: tok, pv: data.pv });
  function once() {
    return fetch('/api/ai/jobs/' + data.job + '/act', { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-Saga': '1' }, body: body });
  }
  return once().catch(function () { return once(); }).then(function (r) {
    if (r.ok || r.status === 404) return null;
    return r.json().catch(function () { return {}; }).then(function (x) { return openUrl(x && x.url ? jobUrl(x.url) : data.url, data); });
  }, function () { return openUrl(data.url, data); });
}

self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {}; }
  var title = String(d.title || 'Saga').slice(0, 120);
  if (d.update) { e.waitUntil(onUpdate(d, title)); return; }  // 2.5: gezeigte Mitteilung anpassen/entfernen (Nachricht geloescht)
  if (isJob(d.kind)) { var jn = jobNote(d); e.waitUntil(self.registration.showNotification(jn.title, jn.opts)); return; }
  var opts = {
    body: String(d.body || '').slice(0, 300),
    tag: d.tag ? String(d.tag).slice(0, 64) : undefined,
    renotify: !!d.tag,
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    timestamp: d.ts ? d.ts * 1000 : Date.now(),
    data: { url: sameOrigin(d.url) }
  };
  if (d.silent) opts.silent = true;   // 2.6 (Bugs-DB 2029-3): Beschaeftigt "ohne Ton"
  if (d.kind === 'call' || d.kind === 'call.group') { // Anruf (2.1; 2.4 Gruppe): bleibt stehen, bis man reagiert; vibriert wie ein Anruf
    opts.requireInteraction = true; opts.vibrate = [400, 200, 400, 200, 400];
  }
  e.waitUntil(self.registration.showNotification(title, opts));
});

// 2.5 (Nutzlast 2): Aktualisierung einer schon gezeigten Mitteilung (gleicher tag). removed: schliessen; sonst still mit
// neuem Text ersetzen - ist keine sichtbar (die eigentliche kam nie an), still anzeigen, solange noch etwas offen ist (n > 0)
function onUpdate(d, title) {
  var tag = d.tag ? String(d.tag).slice(0, 64) : '';
  if (!tag) return Promise.resolve();
  return self.registration.getNotifications({ tag: tag }).then(function (list) {
    if (d.removed) { list.forEach(function (n) { n.close(); }); return; }
    return self.registration.showNotification(title, {
      body: String(d.body || '').slice(0, 300), tag: tag, renotify: false, silent: true,
      icon: '/static/icon-192.png', badge: '/static/icon-192.png',
      timestamp: d.ts ? d.ts * 1000 : Date.now(), data: { url: sameOrigin(d.url) }
    });
  });
}

// 2.5 (Anruf-Huelle, static/shell.js): laeuft in dem Fenster ein Anruf, oeffnet es das Ziel selbst (im Rahmen) - ein
// Neuladen wuerde den Anruf beenden. Antwortet die Seite nicht (aeltere Fassung), wird wie bisher navigiert.
function navAsk(c, url) {
  return new Promise(function (res) {
    var ch = new MessageChannel(), t = setTimeout(function () { res(false); }, 800);
    ch.port1.onmessage = function (ev) { clearTimeout(t); res(!!(ev.data && ev.data.ok)); };
    try { c.postMessage({ op: 'nav', url: url }, [ch.port2]); } catch (x) { clearTimeout(t); res(false); }
  });
}

// Fenster mit url zeigen: dasselbe fokussieren, sonst ein offenes Saga-Fenster dorthin fuehren, sonst ein neues oeffnen.
// job: klappt das nicht (Browser verweigert das Fenster), bleibt ein Hinweis "Bitte in Saga ansehen" mit demselben Ziel.
function openUrl(url, job) {
  return self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      var c = list[i];
      if (c.url === url && 'focus' in c) return c.focus();
    }
    for (var j = 0; j < list.length; j++) {          // offenes Saga-Fenster wiederverwenden (kein Rahmen darin)
      var cj = list[j];
      if (cj.frameType === 'nested' || !('navigate' in cj) || !('focus' in cj)) continue;
      return cj.focus().then(function (w) {
        w = w || cj;
        return navAsk(w, url).then(function (done) { return done ? w : w.navigate(url); });
      });
    }
    return self.clients.openWindow(url);
  }).then(function (w) {
    if (w || !job) return w;
    throw new Error('window');
  }).catch(function (err) {
    if (!job) throw err;
    var tx = JOB_TX[job.lang] || JOB_TX.de;
    return self.registration.showNotification(tx.hint, { body: '', tag: 'saga-job-hint', icon: '/static/icon-192.png', badge: '/static/icon-192.png',
      data: { url: url } });
  });
}

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var data = e.notification.data || {};
  var url = data.url || self.location.origin + '/';
  if (isJob(data.kind) && e.action && e.action !== 'open') { e.waitUntil(jobAct(data, e.action)); return; }   // 2270: Knopf
  e.waitUntil(openUrl(url, isJob(data.kind) ? data : null));      // Klick bzw. "Ansehen" (Deep-Link)
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
        body: JSON.stringify({ type: 'webpush', endpoint: j.endpoint, keys: j.keys, name: '', caps: ['detail', 'actions'] })   // 2270
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
    // 2275: nicht abgeholtes Geteiltes auch - waitUntil haelt den Service Worker bis zum Loeschen wach, Antwort erst danach
    var del = caches.delete(SHARE).catch(function () {}).then(function () { if (port) port.postMessage({ ok: true }); });
    if (e.waitUntil) e.waitUntil(del);
  }
});

self.addEventListener('fetch', function (e) {
  var u;
  try { u = new URL(e.request.url); } catch (x) { return; }
  if (u.origin === self.location.origin && u.pathname === '/share' && e.request.method === 'POST') { e.respondWith(shareIn(e.request)); return; }
  if (u.origin !== self.location.origin || e.request.method !== 'GET') return;
  if (e.request.mode === 'navigate' && u.pathname.indexOf('/_m/') !== 0) {
    // Netz wie immer; nur wenn es gar nicht antwortet (offline, Server weg): Offline-Seite aus dem Cache
    e.respondWith(Promise.resolve(e.preloadResponse).then(function (r) { return r || fetch(e.request); }).catch(function (err) {
      return caches.open(OFF + V).then(function (c) { return c.match(OFF_URL); }).then(function (r) {
        if (!r) throw err;
        return r;
      });
    }));
    return;
  }
  if (u.pathname.indexOf('/_m/') !== 0) return;
  e.respondWith(media(e, u));
});

// --- Teilen-Ziel (2275): POST /share -> Cache (einmal, <= 10 min) -> 303 /saga?share=<schluessel> ----------------------
function shareStr(v, max) {                          // nur Text (Dateien fallen weg), Steuerzeichen ausser \n/\t weg
  return typeof v === 'string' ? v.replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, '').slice(0, max) : '';
}
function sharePrune(c) {                            // abgelaufene und ueberzaehlige Eintraege weg (aelteste zuerst)
  return c.keys().then(function (ks) {
    return Promise.all(ks.map(function (k) { return c.match(k).then(function (r) { return r ? r.clone().json().then(function (d) { return { k: k, at: +d.at || 0 }; }, function () { return { k: k, at: 0 }; }) : { k: k, at: 0 }; }); }));
  }).then(function (xs) {
    xs.sort(function (a, b) { return b.at - a.at; });
    var now = Date.now();
    return Promise.all(xs.filter(function (x, i) { return i >= SHARE_MAX - 1 || now - x.at > SHARE_TTL || x.at > now + 60000; })
      .map(function (x) { return c.delete(x.k); }));
  });
}
function shareIn(req) {
  var to = function (q) { return Response.redirect(self.location.origin + '/saga?' + q, 303); };
  return req.formData().then(function (f) {
    var title = shareStr(f.get('title'), 200).trim(), text = shareStr(f.get('text'), 20000).trim(), url = shareStr(f.get('url'), 2000).trim();
    if (!/^https?:\/\/[^\s<>"]+$/i.test(url)) url = '';      // nur http(s), sonst weg (nie javascript:/data:)
    if (!url && /^https?:\/\/[^\s<>"]{1,2000}$/i.test(text)) { url = text; text = ''; }   // Android: Adresse oft nur in text
    if (!title && !text && !url) return to('share_failed=1');
    var b = new Uint8Array(16); self.crypto.getRandomValues(b);
    var key = Array.prototype.map.call(b, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
    return caches.open(SHARE).then(function (c) {
      return sharePrune(c).then(function () {
        return c.put(new Request('/_share/' + key), new Response(JSON.stringify({ title: title, text: text, url: url, at: Date.now() }),
          { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } }));
      });
    }).then(function () { return to('share=' + key); });
  }).catch(function () { return to('share_failed=1'); });
}

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
