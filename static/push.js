// Saga - Mitteilungen aufs Handy/Desktop (1.7): Einstellungen -> Reiter "Mitteilungen" (static/settings.js ruft
// HKPush.section(body) auf) und stilles Erneuern des Browser-Abos beim Laden. Web Push ueber den Service Worker /sw.js,
// ntfy per Topic-Adresse, App-Geraete (APNs/UnifiedPush) melden sich selbst an und erscheinen in der Liste.
// Alle Texte per textContent. Braucht static/common.js.
(function () {
  'use strict';
  var HK = window.HK; if (!HK || !document.getElementById('hk-user')) return;
  var el = HK.el, api = HK.api;
  var LS = 'hk-push-on';
  var CAT = { share: T('Teilen: jemand teilt eine Seite mit mir oder ändert meine Rolle'), comment: T('Kommentare und Antworten'),
              chat: T('Chat-Nachrichten'), publish: T('Veröffentlichung: ein Admin zieht etwas zurück') };
  var TYPE = { webpush: T('Browser'), ntfy: 'ntfy', unifiedpush: 'UnifiedPush', apns: T('Apple-App') };
  var ICON = { webpush: '🖥', ntfy: '🔔', unifiedpush: '📱', apns: '📱' };

  function ls(k, v) { try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* privat */ } return null; }
  function supported() { return window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window; }
  function iosNoPwa() {
    var ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    return ios && !(window.navigator.standalone || (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches));
  }
  function browserName() {
    var u = navigator.userAgent, b = /Edg\//.test(u) ? 'Edge' : /Firefox\//.test(u) ? 'Firefox' : /Chrome\//.test(u) ? 'Chrome' : /Safari\//.test(u) ? 'Safari' : T('Browser');
    var os = /Android/.test(u) ? 'Android' : /iPhone|iPad|iPod/.test(u) ? 'iOS' : /Mac OS X/.test(u) ? 'macOS' : /Windows/.test(u) ? 'Windows' : /Linux/.test(u) ? 'Linux' : '';
    return os ? T('{browser} auf {os}', { browser: b, os: os }) : b;
  }
  function keyBytes(s) {
    var p = (s + '===='.slice((s.length + 3) % 4)).replace(/-/g, '+').replace(/_/g, '/'), raw = atob(p), a = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) a[i] = raw.charCodeAt(i);
    return a;
  }
  function sameKey(buf, s) {
    if (!buf) return false;
    var a = new Uint8Array(buf), b = keyBytes(s);
    if (a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }
  function reg() { return navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(function () { return navigator.serviceWorker.ready; }); }
  function current() {
    if (!supported()) return Promise.resolve(null);
    return navigator.serviceWorker.getRegistration('/').then(function (r) { return r ? r.pushManager.getSubscription() : null; }).catch(function () { return null; });
  }
  function send(sub) {
    var j = sub.toJSON();
    return api('POST', '/api/push/subscriptions', { type: 'webpush', endpoint: j.endpoint, keys: j.keys, name: browserName() });
  }
  // Einschalten: Erlaubnis -> Service Worker -> Abo (mit dem VAPID-Schluessel des Servers) -> beim Server anmelden
  function enable(vapid) {
    return Notification.requestPermission().then(function (p) {
      if (p !== 'granted') throw new Error(p === 'denied' ? T('Mitteilungen sind für Saga im Browser blockiert – in den Website-Einstellungen erlauben.') : T('Nicht erlaubt.'));
      return reg();
    }).then(function (r) {
      return r.pushManager.getSubscription().then(function (s) {
        if (s && !sameKey(s.options && s.options.applicationServerKey, vapid)) return s.unsubscribe().then(function () { return null; });
        return s;
      }).then(function (s) { return s || r.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(vapid) }); });
    }).then(function (s) { return send(s); }).then(function (d) { ls(LS, '1'); return d; });
  }
  function disable() {
    ls(LS, null);
    return current().then(function (s) {
      if (!s) return null;
      var ep = s.endpoint;
      return s.unsubscribe().catch(function () {}).then(function () { return api('POST', '/api/push/unsubscribe', { endpoint: ep }); });
    });
  }
  // Beim Laden (einmal je Browser-Sitzung): vorhandenes Abo erneut anmelden - nach Abmelden/Passwortwechsel ist es beim
  // Server weg, im Browser aber noch da; neue Schluessel nach einer Erneuerung durch den Browser kommen so auch an.
  function resync() {
    if (ls(LS) !== '1' || !supported() || Notification.permission !== 'granted') return;
    try { if (sessionStorage.getItem('hk-push-sync')) return; sessionStorage.setItem('hk-push-sync', '1'); } catch (e) { return; }
    current().then(function (s) { if (s) send(s).then(function (d) { ls('hk-push-id', String(d.id)); }).catch(function () {}); });
  }
  setTimeout(resync, 3000);

  function when(ts) { return ts ? HK.relTime(ts - Date.now() / 1000) : ''; }
  function row(label, input) {
    var r = el('label', 'lv-set-row'); r.appendChild(el('span', 'lv-set-t', label)); r.appendChild(input); return r;
  }
  function sw(on) { var i = el('input', 'hk-switch'); i.type = 'checkbox'; i.setAttribute('role', 'switch'); i.checked = !!on; return i; }
  function stat(box, txt, cls) { box.textContent = txt || ''; box.className = 'hk-status' + (cls ? ' ' + cls : ''); }

  // =========================================================================================================
  // Einstellungen -> "Mitteilungen"
  // =========================================================================================================
  function section(body) {
    var sec = el('section', 'hk-set hk-set-push'); body.appendChild(sec);
    sec.appendChild(el('h3', null, T('Mitteilungen')));
    sec.appendChild(el('p', 'hk-hint', T('Mitteilungen aufs Handy oder den Computer – auch wenn Saga geschlossen ist. Inhalte von Seiten, Kommentaren und Chats werden nie mitgeschickt.')));
    var box = el('div', 'ps-box'); sec.appendChild(box);
    var S = null;

    function load() {
      return api('GET', '/api/push').then(function (d) { S = d; draw(); })
        .catch(function (err) { box.textContent = T('Nicht geladen: {err}', { err: err.message }); });
    }
    function save(part, st) {
      stat(st, '');
      return api('PUT', '/api/push/settings', part).then(function (d) { S.settings = d.settings; stat(st, T('✓ Gespeichert'), 'ok'); })
        .catch(function (err) { stat(st, err.message, 'err'); throw err; });
    }

    function draw() {
      box.textContent = '';
      if (!S.server.enabled) { box.appendChild(el('p', 'hk-hint', T('Mitteilungen sind auf diesem Server abgeschaltet.'))); return; }
      browserCard();
      devicesCard();
      if (S.server.ntfy) ntfyCard();
      prefsCard();
    }

    // --- Dieser Browser ---------------------------------------------------------------------------------
    function browserCard() {
      var c = el('div', 'ps-card'); box.appendChild(c);
      c.appendChild(el('h4', null, T('Dieser Browser')));
      var line = el('p', 'hk-hint'), bar = el('div', 'hk-actions'), st = el('div', 'hk-status'); st.setAttribute('role', 'status');
      c.appendChild(line); c.appendChild(bar); c.appendChild(st);
      if (!S.server.webpush) { line.textContent = T('Browser-Mitteilungen sind auf diesem Server abgeschaltet.'); return; }
      if (iosNoPwa()) { line.textContent = T('Auf iPhone und iPad: Saga zuerst zum Home-Bildschirm hinzufügen (Teilen → „Zum Home-Bildschirm“), dann dort öffnen und hier einschalten.'); return; }
      if (!supported()) { line.textContent = T('Dieser Browser kann keine Mitteilungen empfangen (oder Saga läuft nicht über https).'); return; }
      current().then(function (s) {
        var mine = s && S.devices.some(function (d) { return d.type === 'webpush' && String(d.id) === ls('hk-push-id'); });
        var on = !!(s && mine && Notification.permission === 'granted');
        if (Notification.permission === 'denied') line.textContent = T('Mitteilungen sind für Saga im Browser blockiert – in den Website-Einstellungen erlauben.');
        else line.textContent = on ? T('✓ Eingeschaltet – dieser Browser bekommt Mitteilungen.') : T('Ausgeschaltet.');
        var go = el('button', 'btn' + (on ? '' : ' btn-primary'), on ? T('Ausschalten') : T('In diesem Browser einschalten')); go.type = 'button';
        if (Notification.permission === 'denied') { bar.remove(); return; }    // blockiert: nur der Hinweis (Knopf haette keine Wirkung)
        bar.appendChild(go);
        go.addEventListener('click', function () {
          go.disabled = true; stat(st, T('Einen Moment …'));
          (on ? disable() : enable(S.server.vapid_public).then(function (d) { ls('hk-push-id', String(d.id)); }))
            .then(function () { stat(st, ''); return load(); })
            .catch(function (err) { stat(st, err.message || String(err), 'err'); go.disabled = false; });
        });
      });
    }

    // --- Geraete (alle Abos des Kontos) --------------------------------------------------------------------
    function devicesCard() {
      var c = el('div', 'ps-card'); box.appendChild(c);
      c.appendChild(el('h4', null, T('Geräte')));
      var list = el('div', 'hk-tok-list'); c.appendChild(list);
      if (!S.devices.length) list.appendChild(el('p', 'hk-hint', T('Noch kein Gerät. Schalte Mitteilungen in diesem Browser ein, trage ntfy ein oder melde eine App an.')));
      S.devices.forEach(function (d) {
        var r = el('div', 'hk-tok ps-dev'), info = el('div', 'hk-hint');
        var top = el('div', 'ps-dev-name', (ICON[d.type] || '🔔') + ' ' + (d.name || TYPE[d.type] || d.type));
        if (d.type === 'webpush' && String(d.id) === ls('hk-push-id')) top.appendChild(el('span', 'ps-tag', T('dieser Browser')));
        if (d.this_device) top.appendChild(el('span', 'ps-tag', T('dieses Gerät')));
        info.appendChild(top);
        var sub = (TYPE[d.type] || d.type) + ' · ' + d.where;
        if (!d.encrypted) sub += ' · ' + T('ohne Namen (Server sieht den Text)');
        info.appendChild(el('div', 'ps-dev-sub', sub));
        var last = d.last_error ? T('Letzter Fehler: {err}', { err: d.last_error }) : d.last_ok ? T('Zuletzt zugestellt {when}', { when: when(d.last_ok) }) : T('Noch nichts zugestellt');
        info.appendChild(el('div', 'ps-dev-sub' + (d.last_error ? ' err' : ''), last));
        if (d.type === 'apns' && !S.server.apns) info.appendChild(el('div', 'ps-dev-sub err', T('Mitteilungen an die Apple-App sind auf diesem Server noch nicht eingerichtet (Admin-Bereich).')));
        r.appendChild(info);
        var st = el('span', 'hk-status'); st.setAttribute('role', 'status');
        var test = el('button', 'btn', T('Test')); test.type = 'button'; test.title = T('Testmitteilung an dieses Gerät senden');
        var del = el('button', 'btn', T('Entfernen')); del.type = 'button';
        test.addEventListener('click', function () {
          test.disabled = true; stat(st, T('Sende …'));
          api('POST', '/api/push/subscriptions/' + d.id + '/test').then(function (x) {
            stat(st, x.ok ? T('✓ Zugestellt') : T('Nicht zugestellt: {err}', { err: x.error || x.result }), x.ok ? 'ok' : 'err');
          }).catch(function (err) { stat(st, err.message, 'err'); }).then(function () { setTimeout(function () { test.disabled = false; }, 5000); });
        });
        del.addEventListener('click', function () {
          HK.ask({ title: T('Gerät entfernen?'), ok: T('Entfernen'), text: T('„{name}“ bekommt dann keine Mitteilungen mehr.', { name: d.name || TYPE[d.type] }) })
            .then(function (yes) {
              if (!yes) return;
              var mineBrowser = d.type === 'webpush' && String(d.id) === ls('hk-push-id');
              (mineBrowser ? disable() : api('DELETE', '/api/push/subscriptions/' + d.id)).then(load).catch(function (err) { stat(st, err.message, 'err'); });
            });
        });
        r.appendChild(test); r.appendChild(del); r.appendChild(st);
        list.appendChild(r);
      });
      if (!S.server.apns) c.appendChild(el('p', 'hk-hint', T('Apps: Android und Linux nutzen ntfy/UnifiedPush; Mitteilungen an die iOS-/macOS-App richtet der Admin ein.')));
    }

    // --- ntfy / UnifiedPush von Hand ------------------------------------------------------------------------
    function ntfyCard() {
      var c = el('details', 'hk-more ps-card'); box.appendChild(c);
      c.appendChild(el('summary', null, T('ntfy hinzufügen')));
      c.appendChild(el('p', 'hk-hint', T('Mit der App ntfy (Android, iPhone, Linux) ein Topic mit schwer zu erratendem Namen abonnieren und dessen Adresse hier eintragen. Über ntfy gehen nie Namen – nur „Neue Nachricht“, „Neuer Kommentar“ usw.')));
      var url = el('input', 'hk-in'); url.type = 'url'; url.placeholder = 'https://ntfy.sh/' + T('geheimes-topic'); url.maxLength = 500;
      var tok = el('input', 'hk-in'); tok.type = 'password'; tok.autocomplete = 'off'; tok.placeholder = T('nur bei geschütztem Topic'); tok.maxLength = 200;
      var nm = el('input', 'hk-in'); nm.type = 'text'; nm.maxLength = 80; nm.placeholder = T('z. B. „Handy“');
      c.appendChild(field(T('Topic-Adresse'), url)); c.appendChild(field(T('Zugangstoken (optional)'), tok)); c.appendChild(field(T('Name'), nm));
      var bar = el('div', 'hk-actions'), add = el('button', 'btn btn-primary', T('Hinzufügen')); add.type = 'button'; bar.appendChild(add); c.appendChild(bar);
      var st = el('div', 'hk-status'); st.setAttribute('role', 'status'); c.appendChild(st);
      add.addEventListener('click', function () {
        add.disabled = true; stat(st, T('Prüfe …'));
        api('POST', '/api/push/subscriptions', { type: 'ntfy', url: url.value.trim(), token: tok.value.trim(), name: nm.value.trim() || 'ntfy' })
          .then(function (d) { return api('POST', '/api/push/subscriptions/' + d.id + '/test').then(load); })
          .catch(function (err) { stat(st, err.message, 'err'); add.disabled = false; });
      });
    }
    function field(label, input) { var l = el('label', 'hk-field'); l.appendChild(el('span', null, label)); l.appendChild(input); return l; }

    // --- Worueber, Namen, Ruhezeiten ------------------------------------------------------------------------
    function prefsCard() {
      var c = el('div', 'ps-card'); box.appendChild(c);
      var st = el('div', 'hk-status'); st.setAttribute('role', 'status');
      c.appendChild(el('h4', null, T('Worüber?')));
      var list = el('div', 'lv-set-list'); c.appendChild(list);
      S.categories.forEach(function (k) {
        var s = sw(S.settings.cats[k]); list.appendChild(row(CAT[k] || k, s));
        s.addEventListener('change', function () { var b = { cats: {} }; b.cats[k] = s.checked; save(b, st).catch(function () { s.checked = !s.checked; }); });
      });
      var nl = el('div', 'lv-set-list ps-gap'); c.appendChild(nl);
      var ns = sw(S.settings.names); nl.appendChild(row(T('Namen zeigen (z. B. „Jonas hat eine Seite mit dir geteilt“)'), ns));
      ns.addEventListener('change', function () { save({ names: ns.checked }, st).catch(function () { ns.checked = !ns.checked; }); });
      c.appendChild(el('h4', null, T('Ruhezeiten')));
      var q = S.settings.quiet, ql = el('div', 'lv-set-list'); c.appendChild(ql);
      var qs = sw(q.on); ql.appendChild(row(T('In dieser Zeit keine Mitteilungen (die Glocke in Saga zeigt alles)'), qs));
      var times = el('div', 'hk-actions ps-quiet'), from = el('input', 'hk-in'), to = el('input', 'hk-in');
      from.type = to.type = 'time'; from.value = q.from; to.value = q.to;
      from.setAttribute('aria-label', T('Ruhezeit von')); to.setAttribute('aria-label', T('Ruhezeit bis'));
      times.appendChild(el('span', null, T('von'))); times.appendChild(from); times.appendChild(el('span', null, T('bis'))); times.appendChild(to);
      times.hidden = !q.on; c.appendChild(times);
      var tz = '';
      try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { tz = ''; }
      function saveQuiet() { save({ quiet: { on: qs.checked, from: from.value || '22:00', to: to.value || '07:00', tz: tz } }, st).catch(function () {}); }
      qs.addEventListener('change', function () { times.hidden = !qs.checked; saveQuiet(); });
      from.addEventListener('change', saveQuiet); to.addEventListener('change', saveQuiet);
      c.appendChild(st);
    }
    load();
  }

  window.HKPush = { section: section, enable: enable, disable: disable, current: current };
})();
