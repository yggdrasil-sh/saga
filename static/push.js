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
              chat: T('Chat-Nachrichten'), publish: T('Veröffentlichung: ein Admin zieht etwas zurück'),
              call: T('Anrufe: es klingelt, verpasste Anrufe'), admin: T('Admin: Backup fehlgeschlagen'),
              assign: T('Zuweisungen: jemand trägt mich in einer Datenbank ein'),
              tasks: T('Aufgaben: tägliche Zusammenfassung (heute fällig / überfällig)') };
  var TYPE = { webpush: T('Browser'), ntfy: 'ntfy', unifiedpush: 'UnifiedPush', apns: T('Apple-App') };
  var ICON = { webpush: '🖥', ntfy: '🔔', unifiedpush: '📱', apns: '📱' };

  function ls(k, v) { try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* privat */ } return null; }
  // Desktop-Huelle (static/host.js): kein Browser-Abo (WKWebView/WebView2 haben kein Web-Push) - Mitteilungen kommen
  // ueber die Huelle selbst; so gibt es auch keine doppelten
  function inHost() { return !!(HK.host && (HK.host.kind === 'mac' || HK.host.kind === 'tauri')); }
  function supported() { return !inHost() && window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window; }
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
    // 2.6 (2270 K-C): Browser nehmen Inhalt (detail) und Knoepfe (actions) von Auftrags-Mitteilungen an - weiter ohne
    // "update" (APP-API.md "Push-Nutzlast 3"); aeltere Server lassen unbekannte Faehigkeiten still weg
    return api('POST', '/api/push/subscriptions', { type: 'webpush', endpoint: j.endpoint, keys: j.keys, name: browserName(), caps: ['detail', 'actions'] });
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

  // 2.6 (2270 K-C): Bereich "jobs" = Mitteilungen der Kollegin; Name der Persona aus dem Ort "Saga" (static/saga.js) bzw.
  // dem KI-Bereich (static/ai.js), sonst einmal nachladen
  function personaName() {
    var st = window.HKAI && HKAI.state ? HKAI.state() : null;
    if (!st && window.HKSaga) st = HKSaga.st;
    return st && st.persona && st.persona.name ? st.persona.name : '';
  }
  function jobsLabel(node) {
    if (!node) return;
    var set = function (nm) { node.textContent = T('Mitteilungen von {name}', { name: nm || T('Saga KI') }); };
    var nm = personaName(); set(nm);
    if (!nm && window.HKSaga && HKSaga.load) HKSaga.load().then(function () { if (personaName()) set(personaName()); }).catch(function () {});
  }
  CAT.jobs = T('Mitteilungen von {name}', { name: T('Saga KI') });
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
    // 2.6 (2029-5): "Erreichbarkeit" (eigener Status) unter den Ruhezeiten - auch, wenn Mitteilungen abgeschaltet sind
    // (erst nach den Mitteilungen gezeichnet - sonst springt der Bereich beim Laden nach unten)
    var rbox = el('div', 'ps-box us-reach-box'), reachOn = false; sec.appendChild(rbox);
    function reachGo() {
      if (reachOn || !HK.userStatus) return;
      reachOn = true; HK.userStatus.load().then(function (v) { if (v) reachCard(rbox); });
    }

    function load() {
      return api('GET', '/api/push').then(function (d) { S = d; draw(); reachGo(); })
        .catch(function (err) { box.textContent = T('Nicht geladen: {err}', { err: err.message }); reachGo(); });
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
      if (inHost()) { line.textContent = T('In der Saga-App kommen Mitteilungen über die App selbst (siehe Geräte unten).'); return; }
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
        var s = sw(S.settings.cats[k]), r = row(CAT[k] || k, s); list.appendChild(r);
        if (k === 'jobs') { s.id = 'ps-cat-jobs'; jobsLabel(r.querySelector('.lv-set-t')); }   // 2270: "Mitteilungen von {Persona}" 
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

  // =========================================================================================================
  // Erreichbarkeit (2.6, Bugs-DB 2029-5): Einstellungen des eigenen Status (PUT /api/account/status/prefs) und Ausnahmen
  // =========================================================================================================
  // Bugs-DB 2393: Host wie der Server (saga/contacts.py _RREF) - auch IPv6 in eckigen Klammern ('~x@[2001:db8::1]')
  var HANDLE = /^~[a-z2-7]{16}(@[a-z0-9.:\[\]\-]{1,260})?$/;
  // Bugs-DB 2390: Treffer anderer Server aus GET /api/share/targets (remote) -> Kennung '~x@host' fuer die Ausnahme oder null.
  // handle ('~x@host', Adressbuch) zuerst; target ('@~x@host' bzw. '@name@host') nur, wenn es selbst eine Kennung ist.
  // Gruppen anderer Server (remote_group) gehen nicht. Wie UserStatusLogic.remoteRef der Android-App.
  function remoteRef(u) {
    if (!u || (u.type && u.type !== 'remote' && u.type !== 'user')) return null;
    var h = String(u.handle || '').trim(), tg = String(u.target || '').trim().replace(/^@/, '');
    return HANDLE.test(h) && h.indexOf('@') > 0 ? h : HANDLE.test(tg) && tg.indexOf('@') > 0 ? tg : null;
  }
  // Bugs-DB 2393: Treffer anderer Server - Label "Name · host" (ggf. " · Unterscheider") ohne den Host, der steht in der
  // Unterzeile (sonst doppelt). Steckt der Host anders im Namen ("Person auf host (tag)"), keine Unterzeile. Wie
  // UserStatusLogic.remoteTitle der Android-App. -> [Zeile, Unterzeile]
  function remoteTitle(label, host) {
    var h = String(host || '').trim(), lb = String(label || '');
    if (!h) return [lb, ''];
    var rest = lb.split(' · ').filter(function (p) { return p.trim().toLowerCase() !== h.toLowerCase(); }).join(' · ').trim();
    var t = !rest || rest.length === lb.length ? lb : rest;
    return [t, t.toLowerCase().indexOf(h.toLowerCase()) >= 0 ? '' : h];
  }
  function reachCard(rbox) {
    var U = HK.userStatus, last = '';
    var c = el('div', 'ps-card us-reach'); c.id = 'us-reach'; rbox.appendChild(c);
    function key(v) { return JSON.stringify([v.prefs, v.exceptions]); }
    function draw() {
      var v = U.view(); if (!v) return;
      last = key(v);
      c.textContent = '';
      c.appendChild(el('h4', null, T('Erreichbarkeit')));
      c.appendChild(el('p', 'hk-hint', T('Wie dich andere erreichen, während du „Beschäftigt“ oder „Nicht stören“ eingestellt hast, in Ruhezeiten und während du telefonierst. Deinen Status setzt du über das Benutzermenü.')));
      var st = el('div', 'hk-status'); st.setAttribute('role', 'status');
      var P = v.prefs || {}, L = v.limits || {}, rs = L.repeat_s || [30, 3600];
      function now(k) { var x = U.view(); return x && x.prefs && k in x.prefs ? x.prefs[k] : P[k]; }   // gespeicherter Stand
      function save(b, undo) {
        st.textContent = ''; st.className = 'hk-status';
        return U.prefs(b).then(function () { st.textContent = T('✓ Gespeichert'); st.className = 'hk-status ok'; })
          .catch(function (err) { st.textContent = err.message; st.className = 'hk-status err'; if (undo) undo(); });
      }
      function rrow(label, hint, input) {
        var r = el('label', 'lv-set-row us-row'), t = el('span', 'lv-set-t');
        t.appendChild(el('span', 'us-row-t', label)); if (hint) t.appendChild(el('span', 'us-row-h', hint));
        r.appendChild(t); r.appendChild(input); return r;
      }
      function swr(k, label, hint) {
        var i = sw(P[k]); i.dataset.k = k;
        i.addEventListener('change', function () { var b = {}; b[k] = i.checked; save(b, function () { i.checked = !!now(k); }); });
        return rrow(label, hint, i);
      }
      function sel(k, label, hint, opts) {
        var s = el('select', 'hk-in us-sel'); s.dataset.k = k;
        opts.forEach(function (o) { var x = el('option', null, o[1]); x.value = o[0]; s.appendChild(x); });
        s.value = P[k];
        s.addEventListener('change', function () { var b = {}; b[k] = s.value; save(b, function () { s.value = now(k); }); });
        return rrow(label, hint, s);
      }
      var l1 = el('div', 'lv-set-list'); c.appendChild(l1);
      l1.appendChild(swr('quiet_dnd', T('Ruhezeiten zählen als „Nicht stören“'), T('In den Ruhezeiten der Mitteilungen gilt dasselbe wie bei „Nicht stören“: Ausnahmen und wiederholte Anrufe kommen durch.')));
      l1.appendChild(swr('quiet_digest', T('Sammelmeldung auch nach Ruhezeiten'), T('Nach „Nicht stören“ kommt eine Meldung mit der Zahl verpasster Nachrichten und Anrufe – mit diesem Schalter auch am Ende einer Ruhezeit.')));
      l1.appendChild(swr('mention_break', T('Erwähnungen kommen durch'), T('Wer dich in einem Chat oder auf einer Seite erwähnt, erreicht dich auch bei „Nicht stören“.')));
      var l2 = el('div', 'lv-set-list ps-gap'); c.appendChild(l2);
      l2.appendChild(sel('in_call', T('Anrufe, während du telefonierst'), T('Anklopfen: ein Hinweis im laufenden Gespräch, ohne Klingeln.'),
        [['knock', T('Anklopfen lassen')], ['busy', T('Besetzt melden')]]));
      l2.appendChild(swr('show_call', T('Andere sehen „Im Anruf“'), T('Nur Chat-Partner, und nur, wenn ihr beide den Online-Status zeigt.')));
      l2.appendChild(sel('busy', T('Bei „Beschäftigt“'), T('„Beschäftigt“ zeigt anderen, dass du gerade wenig Zeit hast.'),
        [['show', T('Mitteilungen wie sonst')], ['silent', T('Mitteilungen ohne Ton')]]));
      // Wiederholungsanruf: Schalter + Sekunden in den Grenzen des Servers
      var rp = sw(P.repeat), sec = el('input', 'hk-in us-repeat-s'); sec.type = 'number'; sec.min = rs[0]; sec.max = rs[1]; sec.step = 1;
      sec.value = P.repeat_s; sec.disabled = !P.repeat; sec.setAttribute('aria-label', T('Zeitraum für wiederholte Anrufe in Sekunden'));
      var rr = rrow(T('Wiederholte Anrufe kommen durch'), T('Ruft dieselbe Person innerhalb von {n} Sekunden noch einmal an, klingelt es trotz „Nicht stören“.', { n: P.repeat_s }), rp);
      rr.classList.add('us-repeat'); l2.appendChild(rr);
      var secRow = el('div', 'lv-set-row us-row us-repeat-row'); var sl = el('label', 'lv-set-t', T('Zeitraum in Sekunden ({min}–{max})', { min: rs[0], max: rs[1] }));
      sl.htmlFor = sec.id = 'us-repeat-s'; secRow.appendChild(sl); secRow.appendChild(sec); l2.appendChild(secRow);
      rp.dataset.k = 'repeat';
      rp.addEventListener('change', function () { sec.disabled = !rp.checked; save({ repeat: rp.checked }, function () { rp.checked = !!now('repeat'); sec.disabled = !rp.checked; }); });
      sec.addEventListener('change', function () {
        var n = Math.round(Number(sec.value));
        if (!isFinite(n) || n < rs[0] || n > rs[1]) { st.textContent = T('Bitte eine Zahl von {min} bis {max} eingeben.', { min: rs[0], max: rs[1] }); st.className = 'hk-status err'; sec.value = now('repeat_s'); return; }
        save({ repeat_s: n }, function () { sec.value = now('repeat_s'); });
      });
      excCard(c, v, save);
      c.appendChild(st);
    }
    var off = U.on(function (v) {                    // anderes Geraet/Tab: neu zeichnen, solange hier nichts bearbeitet wird
      if (!c.isConnected) { off(); return; }
      if (key(v) === last || c.contains(document.activeElement)) return;
      draw();
    });
    draw();
  }
  // Ausnahmen: Personen (Handle bzw. Suche wie beim Teilen) und eigene Gruppen - kommen durch "Nicht stoeren" und Ruhezeiten
  function excCard(c, v, save) {
    var max = (v.limits && v.limits.exceptions) || 100, list = (v.exceptions || []).slice();
    c.appendChild(el('h5', 'us-exc-h', T('Ausnahmen')));
    c.appendChild(el('p', 'hk-hint', T('Diese Personen und Gruppen erreichen dich auch bei „Nicht stören“ und in Ruhezeiten – mit Anrufen und Mitteilungen.')));
    var ul = el('ul', 'us-exc'); ul.setAttribute('aria-label', T('Ausnahmen')); c.appendChild(ul);
    var n = el('p', 'hk-hint us-exc-n'); c.appendChild(n);
    function out(x) { return x.type === 'group' ? { type: 'group', name: x.name } : { type: 'user', ref: x.ref }; }
    function same(a, b) { return a.type === b.type && (a.type === 'group' ? a.name === b.name : a.ref === b.ref); }
    function put(next, focusAfter) {
      return save({ exceptions: next.map(out) }).then(function () {
        var nv = HK.userStatus.view(); list = (nv && nv.exceptions || next).slice(); drawList(); if (focusAfter) focusAfter();
      });
    }
    function drawList() {
      ul.textContent = '';
      if (!list.length) ul.appendChild(el('li', 'hk-hint us-exc-none', T('Noch keine Ausnahmen.')));
      list.forEach(function (x, i) {
        var li = el('li', 'us-exc-i'), nm = x.type === 'group' ? x.name : (x.name || HK.personName(x.ref));
        li.appendChild(el('span', 'us-exc-ic', x.type === 'group' ? '#' : '👤')).setAttribute('aria-hidden', 'true');
        li.appendChild(el('span', 'us-exc-t', nm));
        if (x.type === 'group') li.appendChild(el('span', 'us-exc-k', T('Gruppe')));
        var rm = el('button', 'icon-btn us-exc-rm', '✕'); rm.type = 'button';
        rm.setAttribute('aria-label', T('„{name}“ entfernen', { name: nm }));
        rm.addEventListener('click', function () {
          put(curList().filter(function (y) { return !same(y, x); }), function () {
            var b = ul.querySelectorAll('.us-exc-rm'); (b[Math.min(i, b.length - 1)] || q).focus();
          });
        });
        li.appendChild(rm); ul.appendChild(li);
      });
      n.textContent = T('{n} von {max}', { n: list.length, max: max });
      q.disabled = list.length >= max;
    }
    // Suche (Personen und eigene Gruppen) - Kombinationsfeld mit Trefferliste
    var wrap = el('div', 'us-exc-add'), q = el('input', 'hk-in'); q.type = 'search'; q.id = 'us-exc-q'; q.autocomplete = 'off';
    q.placeholder = T('Person oder Gruppe suchen …'); q.setAttribute('aria-label', T('Ausnahme hinzufügen: Person oder Gruppe suchen'));
    q.setAttribute('role', 'combobox'); q.setAttribute('aria-autocomplete', 'list'); q.setAttribute('aria-expanded', 'false');
    var res = el('ul', 'us-exc-res'); res.id = 'us-exc-res'; res.setAttribute('role', 'listbox'); res.setAttribute('aria-label', T('Treffer')); res.hidden = true;
    q.setAttribute('aria-controls', res.id);
    wrap.appendChild(q); wrap.appendChild(res); c.appendChild(wrap);
    // Liste immer aus dem aktuellen Stand (anderes Geraet/Tab hat vielleicht schon geaendert - nicht ueberschreiben)
    function curList() { var v = HK.userStatus.view(); return (v && v.exceptions || list).slice(); }
    function add(x) {
      var cur = curList();
      if (cur.some(function (y) { return same(y, x); })) { q.value = ''; hide(); list = cur; drawList(); return; }
      put(cur.concat([x]), function () { q.value = ''; hide(); q.focus(); });
    }
    function hide() { res.hidden = true; res.textContent = ''; q.setAttribute('aria-expanded', 'false'); q.removeAttribute('aria-activedescendant'); }
    function opt(label, sub, x) {
      var li = el('li', 'us-exc-o'); li.setAttribute('role', 'option'); li.id = 'us-exc-o' + res.children.length; li.tabIndex = -1;
      li.appendChild(el('span', 'us-exc-t', label)); if (sub) li.appendChild(el('span', 'us-exc-k', sub));
      li.addEventListener('mousedown', function (e) { e.preventDefault(); });
      li.addEventListener('click', function () { add(x); });
      res.appendChild(li);
    }
    var tm = 0, seq = 0, act = -1;
    function mark(i) {
      var o = res.querySelectorAll('[role=option]'); if (!o.length) return;
      act = (i + o.length) % o.length;
      [].forEach.call(o, function (x, j) { x.setAttribute('aria-selected', j === act ? 'true' : 'false'); });
      q.setAttribute('aria-activedescendant', o[act].id); o[act].scrollIntoView({ block: 'nearest' });
    }
    q.addEventListener('input', function () {
      clearTimeout(tm); var s = q.value.trim(), my = ++seq; act = -1;
      if (!s) { hide(); return; }
      tm = setTimeout(function () {
        var direct = HANDLE.test(s) ? [{ type: 'user', ref: s, name: HK.personName(s) }] : [];
        api('GET', '/api/share/targets?q=' + encodeURIComponent(s)).catch(function () { return {}; }).then(function (d) {
          if (my !== seq) return;
          res.textContent = '';
          var seen = {};
          (d.users || []).forEach(function (u) { seen[u.username] = 1; opt(u.name || HK.personName(u.username), u.sub || '', { type: 'user', ref: u.username, name: u.name }); });
          (d.groups || []).forEach(function (g) { opt(g.name, T('Gruppe'), { type: 'group', name: g.name }); });
          (d.remote || []).forEach(function (u) {
            var r = remoteRef(u); if (!r || seen[r]) return; seen[r] = 1;
            // label: "Name · host" (bei Gleichnamigen mit Unterscheidung) - Host nur einmal, in der Unterzeile (2393)
            var tt = remoteTitle(u.label || u.name || HK.personName(r), u.host);
            opt(tt[0], tt[1], { type: 'user', ref: r, name: u.name || '' });
          });
          if (!res.children.length) direct.forEach(function (x) { opt(x.name, x.ref, x); });
          if (!res.children.length) { var e = el('li', 'hk-hint us-exc-none', T('Keine Treffer.')); e.setAttribute('role', 'presentation'); res.appendChild(e); }
          res.hidden = false; q.setAttribute('aria-expanded', 'true');
        });
      }, 250);
    });
    q.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); mark(act + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); mark(act - 1); }
      else if (e.key === 'Enter') { var o = res.querySelectorAll('[role=option]'); if (o.length) { e.preventDefault(); o[act < 0 ? 0 : act].click(); } }
      else if (e.key === 'Escape' && !res.hidden) { e.preventDefault(); e.stopPropagation(); hide(); }
    });
    q.addEventListener('blur', function () { setTimeout(hide, 150); });
    drawList();
  }

  window.HKPush = { section: section, enable: enable, disable: disable, current: current, remoteRef: remoteRef, remoteTitle: remoteTitle,
                    handleOk: function (s) { return HANDLE.test(s); } };
})();
