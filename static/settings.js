/* Benutzermenue (Name oben rechts) + Einstellungen: Konto, Lesezeichen, externe Kalender (CalDAV), Immich, Apps.
   Das App-Passwort geht nur beim Testen/Speichern an den Server und wird dort verschluesselt abgelegt. */
(function () {
  'use strict';
  var el = HK.el, api = HK.api;
  var btn = document.getElementById('hk-user');
  if (!btn) return;
  // In der Saga-App (iOS/macOS): Nachrichten an die App (die App nimmt sie nur von diesem Server an)
  var inApp = /(Saga|Hlidskjalf)App\//.test(navigator.userAgent) && window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.hk;
  function toApp(msg) { if (inApp) window.webkit.messageHandlers.hk.postMessage(msg); }
  btn.addEventListener('click', function () {
    HK.openMenu(btn, function (m) {
      m.appendChild(HK.menuLabel(btn.dataset.name || ''));
      m.appendChild(HK.menuItem(T('Einstellungen'), function () { openSettings(); }, { icon: '⚙' }));
      if (btn.dataset.admin) m.appendChild(HK.menuItem(T('Admin-Bereich'), function () { window.open(btn.dataset.admin, '_blank', 'noopener'); }, { icon: '🛡' }));
      if (inApp) m.appendChild(HK.menuItem(T('App-Einstellungen'), function () { toApp({ action: 'settings' }); }, { icon: '📱' }));
      m.appendChild(el('div', 'menu-sep'));
      m.appendChild(HK.menuItem(T('Abmelden'), function () {
        // Chat-Bild-Cache dieses Kontos (nur Ciphertext, static/chat.js) beim Abmelden leeren; Chat-Schluessel bleiben
        var go = function () { location.href = '/logout'; };
        try {
          if (document.body.dataset.uid) indexedDB.deleteDatabase('saga-chat-blobs-' + document.body.dataset.uid);
          if (!indexedDB.databases) return go();
          indexedDB.databases().then(function (l) {
            l.forEach(function (d) { if (/^saga-chat-blobs-/.test(d.name || '')) indexedDB.deleteDatabase(d.name); });
          }).catch(function () {}).then(go);
        } catch (e) { go(); }
      }, { icon: '↪' }));
    });
  });

  var dlg = null;
  function closeSettings() { if (dlg) { dlg.remove(); dlg = null; document.removeEventListener('keydown', onKey); btn.focus(); } }
  function onKey(e) { if (e.key === 'Escape' && !HK.menuOpen()) { e.preventDefault(); closeSettings(); } }
  function openSettings(focus) {
    closeSettings();
    dlg = el('div', 'hk-modal'); dlg.setAttribute('role', 'dialog'); dlg.setAttribute('aria-modal', 'true'); dlg.setAttribute('aria-label', T('Einstellungen'));
    var card = el('div', 'hk-dialog hk-dialog-set'), head = el('div', 'hk-dlg-head');
    head.appendChild(el('h2', null, T('Einstellungen')));
    var x = el('button', 'icon-btn', '✕'); x.type = 'button'; x.setAttribute('aria-label', T('Schließen')); x.addEventListener('click', closeSettings);
    head.appendChild(x); card.appendChild(head);
    var body = el('div', 'hk-dlg-body'); card.appendChild(body); dlg.appendChild(card);
    dlg.addEventListener('mousedown', function (e) { if (e.target === dlg) closeSettings(); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(dlg);
    // Reiter statt eines langen Dialogs: Konto · Mitteilungen · Lesezeichen · Kalender · Immich · Apps & Geraete
    var tabs = el('div', 'hk-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', T('Einstellungen'));
    body.appendChild(tabs); body.classList.add('tabbed');
    accountSection(body);
    bookmarkSection(body);
    calendarSection(body);
    immichSection(body);
    tokenSection(body);
    if (window.HKPush) HKPush.section(body);                 // Mitteilungen aufs Handy/Desktop (static/push.js)
    var TABS = [['account', T('Konto')], ['push', T('Mitteilungen')], ['bookmarks', T('Lesezeichen')], ['calendar', T('Kalender')], ['immich', 'Immich'], ['apps', T('Apps & Geräte')]];
    if (!window.HKPush) TABS.splice(1, 1);
    var btns = TABS.map(function (t, i) {
      var b = el('button', 'hk-tab', t[1]); b.type = 'button'; b.id = 'hk-tab-' + t[0];
      b.setAttribute('role', 'tab'); b.setAttribute('aria-controls', 'hk-pane-' + t[0]);
      var pane = body.querySelector('.hk-set-' + t[0]);
      if (pane) { pane.id = 'hk-pane-' + t[0]; pane.setAttribute('role', 'tabpanel'); pane.setAttribute('aria-labelledby', b.id); }
      b.addEventListener('click', function () { show(t[0]); });
      b.addEventListener('keydown', function (e) {          // Pfeiltasten wechseln den Reiter (WAI-ARIA Tabs)
        var d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0; if (!d) return;
        e.preventDefault(); var n = TABS[(i + d + TABS.length) % TABS.length][0]; show(n); $tab(n).focus();
      });
      tabs.appendChild(b); return b;
    });
    function $tab(k) { return btns[TABS.findIndex(function (t) { return t[0] === k; })]; }
    function show(k) {
      TABS.forEach(function (t, i) {
        var on = t[0] === k, pane = body.querySelector('.hk-set-' + t[0]);
        btns[i].setAttribute('aria-selected', on ? 'true' : 'false'); btns[i].tabIndex = on ? 0 : -1;
        if (pane) pane.hidden = !on;
      });
      body.scrollTop = 0;
      reveal($tab(k));
    }
    // Handy: Leiste scrollt waagrecht - Verlauf am Rand, aktiver Reiter immer ganz sichtbar
    var reveal = HK.scrollFade(tabs);
    var start = typeof focus === 'string' && $tab(focus) ? focus : 'account';
    show(start);
  }

  // --- Abschnitt "Immich" (eigener API-Schluessel; der Schluessel verlaesst den Server nie wieder) ------------------
  function immichSection(body) {
    var sec = el('section', 'hk-set hk-set-immich'); body.appendChild(sec);
    sec.appendChild(el('h3', null, 'Immich'));
    var box = el('div'); sec.appendChild(box);
    function load() {
      api('GET', '/api/settings/immich').then(function (s) {
        box.textContent = '';
        if (s.connected) {
          box.appendChild(el('p', null, (s.name ? T('✅ Verbunden als {name}', { name: s.name }) : T('✅ Verbunden')) + (s.url ? ' (' + s.url.replace(/^https?:\/\//, '') + ')' : '') +
            ' – ' + T('Bilder aus Immich gibt es beim Titelbild, beim Icon und im „/“-Menü („Bild aus Immich“).')));
          var off = el('button', 'btn', T('Trennen')); off.type = 'button';
          off.addEventListener('click', function () { api('DELETE', '/api/settings/immich').then(load); });
          box.appendChild(off); return;
        }
        box.appendChild(el('p', 'hk-hint', T('Adresse deines Immich (z. B. https://immich.example.org). In Immich unter Kontoeinstellungen → API-Schlüssel einen neuen Schlüssel anlegen mit den Rechten album.read, asset.read, asset.view, asset.download und user.read, und hier einfügen. Saga sieht dann nur deine eigenen Fotos.')));
        var url = el('input', 'hk-in'); url.type = 'url'; url.placeholder = 'https://immich.example.org'; url.value = s.suggest || '';
        box.appendChild(field(T('Immich-Adresse'), url));
        var inp = el('input', 'hk-in'); inp.type = 'password'; inp.autocomplete = 'off'; inp.placeholder = T('API-Schlüssel');
        box.appendChild(field(T('API-Schlüssel'), inp));
        var go = el('button', 'btn btn-primary', T('Verbinden')); go.type = 'button';
        var goBar = el('div', 'hk-actions'); goBar.appendChild(go); box.appendChild(goBar);
        var msg = el('p', 'hk-hint'); box.appendChild(msg);
        go.addEventListener('click', function () {
          msg.textContent = T('Prüfe …'); go.disabled = true;
          api('POST', '/api/settings/immich', { url: url.value.trim(), apikey: inp.value.trim() }).then(function () { inp.value = ''; load(); })
            .catch(function (e) { msg.textContent = e.message; go.disabled = false; });
        });
      }).catch(function (e) { box.textContent = T('Nicht geladen: {err}', { err: e.message }); });
    }
    load();
  }

  // --- Abschnitt "Kalender" ----------------------------------------------------------
  function calendarSection(body) {
    var sec = el('section', 'hk-set hk-set-calendar'); body.appendChild(sec);
    sec.appendChild(el('h3', null, T('Kalender')));
    sec.appendChild(el('p', 'hk-hint', T('Externe Kalender (CalDAV) wie Nextcloud, mailcow, Radicale oder iCloud verbinden. Saga zeigt deren Termine an und kann Termine anlegen und ändern. Tipp: Ein eigenes App-Passwort nur für Kalender/DAV anlegen.')));
    var list = el('div', 'hk-cal-list'); sec.appendChild(list);
    var addBtn = el('button', 'btn', T('+ Kalender verbinden')); addBtn.type = 'button'; sec.appendChild(addBtn);
    var form = el('div', 'hk-cal-form'); form.hidden = true; sec.appendChild(form);
    var allowed = { hosts: [], private: false };
    function load() {
      api('GET', '/api/settings/calendars').then(function (d) {
        allowed = { hosts: d.allowed || [], private: !!d.private }; list.textContent = '';
        if (!d.accounts.length) list.appendChild(el('p', 'hk-hint', T('Noch kein Kalender verbunden.')));
        d.accounts.forEach(function (a) { list.appendChild(accountCard(a, load)); });
      }).catch(function (err) { list.textContent = T('Nicht geladen: {err}', { err: err.message }); });
    }
    addBtn.addEventListener('click', function () { form.hidden = false; addBtn.hidden = true; newForm(form, allowed, function () { form.hidden = true; addBtn.hidden = false; load(); }); });
    load();
  }

  function field(label, input) { var l = el('label', 'hk-field'); l.appendChild(el('span', null, label)); l.appendChild(input); return l; }
  function input(type, value, ph) { var i = el('input', 'hk-in'); i.type = type; i.value = value || ''; if (ph) i.placeholder = ph; return i; }
  function status(box, txt, cls) { box.textContent = txt; box.className = 'hk-status' + (cls ? ' ' + cls : ''); }

  // Kalenderliste mit "anzeigen" + Farbe + Name
  function calPicker(cals) {
    var wrap = el('div', 'hk-cals');
    cals.forEach(function (c) {
      var row = el('div', 'hk-cal-row'), cb = el('input'); cb.type = 'checkbox'; cb.checked = c.show !== false; cb.setAttribute('aria-label', T('{name} anzeigen', { name: c.name }));
      var col = el('input'); col.type = 'color'; col.value = /^#[0-9a-f]{6}$/i.test(c.color || '') ? c.color : '#4184e4'; col.setAttribute('aria-label', T('Farbe für {name}', { name: c.name }));
      var nm = input('text', c.name); nm.maxLength = 80; nm.setAttribute('aria-label', T('Anzeigename'));
      row.appendChild(cb); row.appendChild(col); row.appendChild(nm);
      if (c.writable === false) row.appendChild(el('span', 'hk-hint', T('nur lesen')));
      row._get = function () { return { href: c.href, name: nm.value.trim(), color: col.value, show: cb.checked }; };
      wrap.appendChild(row);
    });
    wrap.value = function () { return [].map.call(wrap.children, function (r) { return r._get(); }); };
    return wrap;
  }

  var PRESETS = [['nextcloud', 'Nextcloud', 'https://HOST/remote.php/dav/'], ['mailcow', 'mailcow (SOGo)', 'https://HOST/SOGo/dav/'],
    ['other', T('Andere CalDAV-Adresse'), 'https://']];
  function newForm(form, allowed, done) {
    form.textContent = '';
    var kind = el('select', 'hk-in'); PRESETS.forEach(function (p) { var o = el('option', null, p[1]); o.value = p[0]; kind.appendChild(o); });
    var url = input('url', PRESETS[0][2]), user = input('text', '', T('Benutzername oder E-Mail')), pw = input('password', '', T('App-Passwort'));
    user.autocomplete = 'username'; pw.autocomplete = 'new-password';
    kind.addEventListener('change', function () {
      var p = PRESETS.find(function (x) { return x[0] === kind.value; });
      url.value = p[2];
    });
    var label = input('text', '', T('optional, z. B. „Privat“')); label.maxLength = 60;
    [field(T('Anbieter'), kind), field(T('CalDAV-Adresse'), url), field(T('Benutzername'), user), field(T('App-Passwort'), pw), field(T('Bezeichnung'), label)].forEach(function (f) { form.appendChild(f); });
    form.appendChild(el('p', 'hk-hint', T('Öffentliche Kalender-Server (https) gehen immer. Interne Server: {what}', {
      what: allowed.private ? T('erlaubt.') : (allowed.hosts.length ? T('nur {hosts}', { hosts: allowed.hosts.join(', ') }) : T('nur nach Freigabe im Admin-Bereich')) + '.' })));
    var st = el('div', 'hk-status'), bar = el('div', 'hk-actions'), test = el('button', 'btn', T('Verbindung testen')), cancel = el('button', 'btn', T('Abbrechen'));
    test.type = cancel.type = 'button'; bar.appendChild(test); bar.appendChild(cancel); form.appendChild(bar); form.appendChild(st);
    var picker = null, save = null;
    cancel.addEventListener('click', function () { form.textContent = ''; done(); });
    test.addEventListener('click', function () {
      status(st, T('Verbinde …')); test.disabled = true;
      api('POST', '/api/settings/calendars/test', { base_url: url.value.trim(), username: user.value.trim(), password: pw.value })
        .then(function (d) {
          status(st, T('✓ Verbunden – {n} Kalender gefunden. Auswählen und speichern:', { n: d.calendars.length }), 'ok');
          if (picker) picker.remove(); if (save) save.remove();
          picker = calPicker(d.calendars); form.appendChild(picker);
          save = el('button', 'btn btn-primary', T('Verbinden & speichern')); save.type = 'button'; form.appendChild(save);
          save.addEventListener('click', function () {
            save.disabled = true;
            api('POST', '/api/settings/calendars', { base_url: url.value.trim(), username: user.value.trim(), password: pw.value, label: label.value.trim(), calendars: picker.value() })
              .then(function () { pw.value = ''; HK.toast(T('Kalender verbunden')); form.textContent = ''; done(); })
              .catch(function (err) { save.disabled = false; status(st, err.message, 'err'); });
          });
        })
        .catch(function (err) { status(st, err.message, 'err'); })
        .then(function () { test.disabled = false; });
    });
  }

  function accountCard(a, reload) {
    var card = el('div', 'hk-acc'), h = el('div', 'hk-acc-head');
    h.appendChild(el('strong', null, a.label || a.username));
    h.appendChild(el('span', 'hk-hint', a.username + ' · ' + a.base_url.replace(/^https?:\/\//, '').split('/')[0] + ' · ' + T('Passwort gespeichert ✓')));
    card.appendChild(h);
    var picker = calPicker(a.calendars); card.appendChild(picker);
    var st = el('div', 'hk-status'), bar = el('div', 'hk-actions');
    var save = el('button', 'btn', T('Auswahl speichern')), more = el('button', 'btn', T('Neu einlesen')), pwb = el('button', 'btn', T('Passwort ändern')), del = el('button', 'btn btn-danger', T('Entfernen'));
    [save, more, pwb, del].forEach(function (b) { b.type = 'button'; bar.appendChild(b); });
    card.appendChild(bar); card.appendChild(st);
    save.addEventListener('click', function () {
      status(st, T('Speichere …'));
      api('PATCH', '/api/settings/calendars/' + a.id, { calendars: picker.value() }).then(function () { status(st, T('✓ Gespeichert'), 'ok'); })
        .catch(function (err) { status(st, err.message, 'err'); });
    });
    more.addEventListener('click', function () {          // neu angelegte Kalender des Kontos holen
      status(st, T('Lese Kalender …'));
      api('POST', '/api/settings/calendars/test', { id: a.id }).then(function (d) {
        var cur = picker.value(), by = {}; cur.forEach(function (c) { by[c.href] = c; });
        var merged = d.calendars.map(function (c) { return by[c.href] ? Object.assign({}, c, by[c.href]) : Object.assign({}, c, { show: false }); });
        var np = calPicker(merged); card.replaceChild(np, picker); picker = np;
        status(st, T('{n} Kalender – Auswahl speichern nicht vergessen.', { n: d.calendars.length }), 'ok');
      }).catch(function (err) { status(st, err.message, 'err'); });
    });
    pwb.addEventListener('click', function () {
      var pw = input('password', '', T('Neues App-Passwort')); pw.autocomplete = 'new-password';
      var ok = el('button', 'btn', T('Prüfen & speichern')); ok.type = 'button';
      var row = el('div', 'hk-actions'); row.appendChild(pw); row.appendChild(ok); card.insertBefore(row, st); pw.focus();
      ok.addEventListener('click', function () {
        status(st, T('Prüfe …'));
        api('PATCH', '/api/settings/calendars/' + a.id, { password: pw.value }).then(function () { pw.value = ''; row.remove(); status(st, T('✓ Neues Passwort gespeichert'), 'ok'); })
          .catch(function (err) { status(st, err.message, 'err'); });
      });
    });
    del.addEventListener('click', function () {
      if (!confirm(T('Kalender-Zugang „{name}“ entfernen? (Die Termine im Kalender selbst bleiben erhalten.)', { name: a.label || a.username }))) return;
      api('DELETE', '/api/settings/calendars/' + a.id).then(reload).catch(function (err) { status(st, err.message, 'err'); });
    });
    return card;
  }
  // --- Abschnitt "Apps & Geraete": Zugangsschluessel fuer die iOS-App (Teilen, Widget) ---------
  var fDate = new Intl.DateTimeFormat(window.SAGA_LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric' });
  function tokenSection(body) {
    var sec = el('section', 'hk-set hk-set-apps'); body.appendChild(sec);
    sec.appendChild(el('h3', null, T('Apps & Geräte')));
    sec.appendChild(el('p', 'hk-hint', T('Angemeldete Apps bleiben angemeldet. Zu jeder App gehört ein kleiner Schlüssel für Widget, Teilen und Kurzbefehle (darf nur ablegen und „Heute“ lesen). Verlorenes Gerät → abmelden, dann ist beides weg.')));
    var devs = el('div', 'hk-tok-list'); sec.appendChild(devs);
    // App per QR-Code anmelden: Code gilt 2 Minuten und einmal; die App uebernimmt daraus auch die Server-Adresse
    var qrBar = el('div', 'hk-actions'), qrBtn = el('button', 'btn', T('📷 App per QR-Code anmelden')); qrBtn.type = 'button';
    qrBar.appendChild(qrBtn); sec.appendChild(qrBar);
    var qrBox = el('div', 'hk-qr'); qrBox.hidden = true; sec.appendChild(qrBox);
    var qrTimer = null;
    function qrStop() { if (qrTimer) { clearInterval(qrTimer); qrTimer = null; } }
    qrBtn.addEventListener('click', function () {
      qrStop(); qrBtn.disabled = true;
      api('POST', '/api/settings/pair').then(function (d) {
        qrBox.textContent = ''; qrBox.hidden = false;
        var img = el('img'); img.src = d.qr; img.alt = T('QR-Code zum Anmelden der App'); img.width = 220; img.height = 220;
        var txt = el('div'), st = el('p', 'hk-status');
        txt.appendChild(el('p', null, T('In der App (iPhone, iPad, Android) auf „Mit QR-Code anmelden“ tippen und diesen Code scannen – oder mit der Kamera-App scannen.')));
        // Kopplungslink fuer Desktop-Apps (Mac, Linux): gleicher Code, gleiche Regeln (2 Minuten, einmal)
        var lnk = null;
        if (d.link) {
          lnk = el('div', 'hk-qr-link');
          lnk.appendChild(el('p', 'hk-hint', T('Mac- oder Linux-App: Link kopieren und in der App bei der Anmeldung als Kopplungslink einfügen.')));
          var row = el('div', 'hk-actions'), li = el('input', 'hk-in hk-tokval'); li.type = 'text'; li.readOnly = true; li.value = d.link;
          li.setAttribute('aria-label', T('Kopplungslink'));
          var cp = el('button', 'btn', T('Link kopieren')); cp.type = 'button';
          cp.addEventListener('click', function () {
            li.select();
            (navigator.clipboard ? navigator.clipboard.writeText(d.link) : Promise.reject()).then(function () { HK.toast(T('Kopiert')); },
              function () { try { document.execCommand('copy'); HK.toast(T('Kopiert')); } catch (e) { /* markiert - selbst kopieren */ } });
          });
          row.appendChild(li); row.appendChild(cp); lnk.appendChild(row); txt.appendChild(lnk);
        }
        txt.appendChild(el('p', 'hk-hint', T('Der Code gilt 2 Minuten und nur einmal. Zeig ihn niemandem – wer ihn scannt, ist als du angemeldet.')));
        txt.appendChild(st); qrBox.appendChild(img); qrBox.appendChild(txt);
        var end = Date.now() + d.expires_in * 1000;
        qrTimer = setInterval(function () {
          var left = Math.max(0, Math.round((end - Date.now()) / 1000));
          api('GET', '/api/settings/pair/' + encodeURIComponent(d.id)).then(function (s) {
            if (s.used_by) {
              qrStop(); img.remove(); if (lnk) lnk.remove(); status(st, T('✓ „{name}“ ist angemeldet.', { name: s.used_by }), 'ok'); load();
            } else if (s.expired || !left) {
              qrStop(); img.remove(); if (lnk) lnk.remove(); status(st, T('Abgelaufen – bei Bedarf einen neuen Code erzeugen.'), 'err');
            } else { status(st, T('Noch {n} s gültig …', { n: left })); }
          }).catch(function () {});
        }, 2000);
      }).catch(function (err) { HK.toast(err.message); }).then(function () { qrBtn.disabled = false; });
    });
    // weitere Schluessel (von Hand, z. B. fuer Kurzbefehle/Skripte oder aeltere Apps) - eingeklappt
    var more = el('details', 'hk-more'), sum = el('summary', null, T('Weitere Schlüssel')); more.appendChild(sum); sec.appendChild(more);
    more.appendChild(el('p', 'hk-hint', T('Von Hand erzeugte Schlüssel, z. B. für Kurzbefehle oder Skripte. Sie gehören zu keinem angemeldeten Gerät.')));
    var list = el('div', 'hk-tok-list'); more.appendChild(list);
    var bar = el('div', 'hk-actions'), nm = el('input', 'hk-in'); nm.type = 'text'; nm.maxLength = 60; nm.placeholder = T('Name, z. B. „Kurzbefehle“');
    var add = el('button', 'btn', T('+ Schlüssel erzeugen')); add.type = 'button'; bar.appendChild(nm); bar.appendChild(add); more.appendChild(bar);
    var out = el('div'); more.appendChild(out);
    function when(t) { return fDate.format(new Date(t * 1000)); }
    function load() {
      api('GET', '/api/settings/apps').then(function (d) {
        devs.textContent = ''; list.textContent = '';
        if (!d.devices.length) devs.appendChild(el('p', 'hk-hint', T('Keine App angemeldet.')));
        d.devices.forEach(function (k) {
          var row = el('div', 'hk-tok');
          row.appendChild(el('strong', null, k.name));
          if (k.current) row.appendChild(el('span', 'hk-tok-me', T('Dieses Gerät')));            // BUG-046
          if (k.prefix) row.appendChild(el('code', null, k.prefix + '…'));                          // unterscheidet gleich benannte
          row.appendChild(el('span', 'hk-hint', T('angemeldet {date}', { date: when(k.created) }) + (k.last_used ? ' · ' + T('zuletzt aktiv {date}', { date: when(k.last_used) }) : '') +
            (k.key ? ' · ' + T('mit Widget-Schlüssel') : '')));
          var rv = el('button', 'btn btn-danger', T('Abmelden')); rv.type = 'button';
          rv.addEventListener('click', function () {
            if (!confirm(k.current ? T('Das ist dieses Gerät. „{name}“ abmelden? Du wirst hier sofort abgemeldet und musst dich neu anmelden.', { name: k.name })
                                   : T('„{name}“ abmelden? Die App muss sich neu anmelden; Widget und Teilen auf diesem Gerät verlieren den Zugang.', { name: k.name }))) return;
            api('DELETE', '/api/settings/devices/' + k.id).then(function () { if (k.current) location.reload(); else load(); })
              .catch(function (err) { HK.toast(err.message); });
          });
          row.appendChild(rv); devs.appendChild(row);
        });
        sum.textContent = T('Weitere Schlüssel') + (d.keys.length ? ' (' + d.keys.length + ')' : '');
        if (!d.keys.length) list.appendChild(el('p', 'hk-hint', T('Keine.')));
        d.keys.forEach(function (k) {
          var row = el('div', 'hk-tok');
          row.appendChild(el('strong', null, k.name));
          row.appendChild(el('code', null, k.prefix + '…'));
          row.appendChild(el('span', 'hk-hint', T('erstellt {date}', { date: when(k.created) }) + ' · ' + (k.last_used ? T('zuletzt benutzt {date}', { date: when(k.last_used) }) : T('noch nie benutzt'))));
          var rv = el('button', 'btn btn-danger', T('Widerrufen')); rv.type = 'button';
          rv.addEventListener('click', function () {
            if (!confirm(T('Schlüssel „{name}“ widerrufen?', { name: k.name }))) return;
            api('DELETE', '/api/settings/tokens/' + k.id).then(load).catch(function (err) { HK.toast(err.message); });
          });
          row.appendChild(rv); list.appendChild(row);
        });
      }).catch(function (err) { devs.textContent = T('Nicht geladen: {err}', { err: err.message }); });
    }
    add.addEventListener('click', function () {
      add.disabled = true;
      api('POST', '/api/settings/tokens', { name: nm.value.trim() }).then(function (d) {
        nm.value = ''; out.textContent = '';
        var box = el('div', 'hk-newtok');
        box.appendChild(el('p', null, '🔑 ' + T('Neuer Schlüssel für „{name}“ – nur jetzt sichtbar!', { name: d.name })));
        var code = el('input', 'hk-in hk-tokval'); code.type = 'text'; code.readOnly = true; code.value = d.token; box.appendChild(code);
        var cp = el('button', 'btn', T('Kopieren')); cp.type = 'button'; box.appendChild(cp);
        cp.addEventListener('click', function () {
          code.select();
          (navigator.clipboard ? navigator.clipboard.writeText(d.token) : Promise.reject()).then(function () { HK.toast(T('Kopiert')); }, function () { document.execCommand('copy'); HK.toast(T('Kopiert')); });
        });
        if (inApp) {
          var use = el('button', 'btn', T('In dieser App verwenden')); use.type = 'button';
          use.addEventListener('click', function () { toApp({ action: 'token', token: d.token }); use.textContent = T('✓ Übergeben'); use.disabled = true; });
          box.appendChild(use);
        }
        out.appendChild(box); code.focus(); code.select(); load();
      }).catch(function (err) { HK.toast(err.message); }).then(function () { add.disabled = false; });
    });
    load();
  }

  // Sprache der Web-Oberflaeche (Konto-Einstellung; "Automatisch" = Standard des Servers bzw. Browsersprache)
  function langField(box, a) {
    var sel = el('select', 'hk-in');
    (a.langs || []).forEach(function (l) { var o = el('option', null, l.name); o.value = l.code; sel.appendChild(o); });
    sel.value = a.lang || '';
    var st = el('div', 'hk-status');
    sel.addEventListener('change', function () {
      sel.disabled = true;
      api('PUT', '/api/account/lang', { lang: sel.value }).then(function () { location.reload(); })
        .catch(function (err) { sel.disabled = false; status(st, err.message, 'err'); });
    });
    box.appendChild(field(T('Sprache'), sel)); box.appendChild(st);
  }

  // Oeffentlicher Name (Veroeffentlichen): eigener Block unten im Konto mit eigenem Knopf - steht in jeder oeffentlichen
  // Adresse /@name/..., nie der Anmeldename. Pruefung (gueltig + frei) schon beim Tippen, Ergebnis direkt unter dem Feld,
  // dazu live "Neue Adresse: ... (bisher ...)"; Rueckfrage nur beim Aendern eines Namens mit oeffentlichen Seiten.
  function publicNameField(box) {
    var wrap = el('div', 'hk-pubname'); box.appendChild(wrap);
    wrap.appendChild(el('h4', null, T('Öffentlicher Name')));
    api('GET', '/api/account/public-name').then(function (p) {
      var inp = input('text', p.name || '', p.suggested || ''); inp.maxLength = 32; inp.spellcheck = false; inp.setAttribute('autocapitalize', 'off');
      inp.setAttribute('aria-label', T('Öffentlicher Name'));
      var col = el('div', 'pub-fcol'), fm = el('p', 'hk-fieldmsg'), st = el('div', 'hk-status'), live = el('p', 'pub-newurl');
      st.setAttribute('role', 'status');
      col.appendChild(inp); col.appendChild(fm); col.appendChild(st); col.appendChild(live);
      wrap.appendChild(field(T('Name'), col));
      var setErr = HK.fieldMsg(inp, fm, '');
      var hint = el('p', 'hk-hint'); wrap.appendChild(hint);
      function urlFor(n) { return (p.url ? p.url.replace(/\/@[^\/]*$/, '') : location.origin) + '/@' + n; }
      function showHint() {
        hint.textContent = '';
        if (p.name) hint.appendChild(HK.fmtNodes(T('Steht in den Adressen deiner öffentlichen Seiten ({url}). Beim Ändern funktionieren alte Links nicht mehr.'), { url: HK.urlNode(p.url) }));
        else hint.textContent = T('Steht in den Adressen deiner öffentlichen Seiten (…/@name/…) und ist für alle sichtbar – nicht dein Anmeldename. Wird beim ersten Veröffentlichen festgelegt.');
      }
      function showLive(v) {
        live.textContent = ''; live.hidden = !(v && p.name && v !== p.name);
        if (!live.hidden) live.appendChild(HK.fmtNodes(T('Neue Adresse: {url} (bisher {old})'), { url: HK.urlNode(urlFor(v)), old: HK.urlNode(p.url) }));
      }
      showHint(); showLive('');
      var bar = el('div', 'hk-actions'), sv = el('button', 'btn', T('Namen speichern')); sv.type = 'button';
      sv.disabled = true; bar.appendChild(sv); wrap.appendChild(bar);
      var tmr = null;
      function check() {
        var v = inp.value.trim().toLowerCase();
        clearTimeout(tmr); sv.disabled = true; status(st, ''); setErr('');      // alte Meldung verschwindet beim Weitertippen
        showLive(v);
        if (!v || v === p.name) return;
        tmr = setTimeout(function () {
          api('PUT', '/api/account/public-name', { name: v, check: true }).then(function (r) {
            if (inp.value.trim().toLowerCase() !== v) return;
            if (r.ok) status(st, T('✓ Frei'), 'ok'); else setErr(r.error);
            sv.disabled = !r.ok;
          }).catch(function (err) { setErr(err.message); });
        }, 250);
      }
      inp.addEventListener('input', check);
      inp.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !sv.disabled) { e.preventDefault(); sv.click(); } });
      function save(v) {
        sv.disabled = true;
        api('PUT', '/api/account/public-name', { name: v }).then(function (d) {
          p.name = d.name; p.url = d.url; inp.value = d.name; showHint(); showLive(''); status(st, T('✓ Gespeichert'), 'ok');
        }).catch(function (err) { status(st, ''); setErr(err.message); sv.disabled = false; });
      }
      sv.addEventListener('click', function () {
        var v = inp.value.trim().toLowerCase();
        if (!(p.name && v !== p.name && p.published)) { save(v); return; }
        HK.ask({ title: T('Öffentlichen Namen ändern?'), ok: T('Ändern'),
                 text: T('Links auf deine veröffentlichten Seiten ändern sich. Alte Links funktionieren nicht mehr.') })
          .then(function (yes) { if (yes) save(v); else inp.focus(); });
      });
    }).catch(function () { wrap.remove(); });
  }

  // --- Profilbild (saga/avatars.py): im Benutzer-Knopf oben und unter Einstellungen -> Konto ------------------------
  function hueOf(s) { var h = 0; s = String(s || ''); for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; }
  function headAvatar(url) {
    var av = btn.querySelector('.hk-user-av');
    if (!av) {                                            // "👤 Name ▾": das Symbol wird zum Platz fuer das Profilbild
      var first = btn.firstChild;
      av = el('span', 'hk-user-av', '👤'); av.setAttribute('aria-hidden', 'true');
      if (first && first.nodeType === 3 && first.nodeValue.indexOf('👤') >= 0) first.nodeValue = first.nodeValue.replace('👤', '');
      btn.insertBefore(av, btn.firstChild);
    }
    var old = av.querySelector('.hk-av-img'); if (old) old.remove();
    av.classList.remove('hk-av-on');
    HK.avatarImg(av, url, 24);
  }
  if (btn.dataset.avatar) headAvatar(btn.dataset.avatar);

  // Zuschneiden im Browser: quadratischer Ausschnitt (verschieben + zoomen), heraus kommt ein 512er-PNG. Der Server
  // kodiert es noch einmal neu (Groessen, ohne Metadaten).
  function cropDialog(file) {
    return new Promise(function (resolve) {
      var url = URL.createObjectURL(file), img = new Image();
      img.onerror = function () {
        URL.revokeObjectURL(url);
        // HEIC (iPhone) zeigen nur manche Browser an: dann ungeschnitten hochladen, der Server schneidet mittig zu
        if (/^image\/hei[cf]$/i.test(file.type) || /\.hei[cf]$/i.test(file.name || '')) { resolve(file); return; }
        HK.toast(T('Das Bild ließ sich nicht lesen.')); resolve(null);
      };
      img.onload = function () {
        var ov = el('div', 'hk-modal'); ov.setAttribute('role', 'dialog'); ov.setAttribute('aria-modal', 'true'); ov.setAttribute('aria-label', T('Profilbild zuschneiden'));
        var card = el('div', 'hk-dialog'), head = el('div', 'hk-dlg-head'); head.appendChild(el('h2', null, T('Profilbild zuschneiden')));
        var bd = el('div', 'hk-dlg-body hk-crop'), stage = el('div', 'hk-crop-stage'), cv = el('canvas'), ring = el('div', 'hk-crop-ring');
        cv.width = cv.height = 560; stage.appendChild(cv); stage.appendChild(ring);
        var zoom = el('input'); zoom.type = 'range'; zoom.min = '1'; zoom.max = '4'; zoom.step = '0.01'; zoom.value = '1'; zoom.setAttribute('aria-label', T('Zoom'));
        bd.appendChild(stage); bd.appendChild(zoom); bd.appendChild(el('p', 'hk-hint', T('Ziehen zum Verschieben, Regler zum Vergrößern.')));
        var bar = el('div', 'hk-actions'), ok = el('button', 'btn btn-primary', T('Übernehmen')), no = el('button', 'btn', T('Abbrechen'));
        ok.type = no.type = 'button'; bar.appendChild(no); bar.appendChild(ok); bd.appendChild(bar);
        card.appendChild(head); card.appendChild(bd); ov.appendChild(card); document.body.appendChild(ov);
        var W = img.naturalWidth, H = img.naturalHeight, base = Math.min(W, H), z = 1, cx = W / 2, cy = H / 2;
        function clamp() { var half = base / z / 2; cx = Math.min(Math.max(cx, half), W - half); cy = Math.min(Math.max(cy, half), H - half); }
        function draw(target, n) {
          var g = target.getContext('2d'), side = base / z; clamp();
          g.clearRect(0, 0, n, n); g.imageSmoothingQuality = 'high';
          g.drawImage(img, cx - side / 2, cy - side / 2, side, side, 0, 0, n, n);
        }
        draw(cv, 560);
        zoom.addEventListener('input', function () { z = parseFloat(zoom.value) || 1; draw(cv, 560); });
        var drag = null;
        stage.addEventListener('pointerdown', function (e) { drag = { x: e.clientX, y: e.clientY, cx: cx, cy: cy }; stage.setPointerCapture(e.pointerId); });
        stage.addEventListener('pointermove', function (e) {
          if (!drag) return;
          var k = (base / z) / stage.clientWidth;          // Bildpunkte je Bildschirmpunkt
          cx = drag.cx - (e.clientX - drag.x) * k; cy = drag.cy - (e.clientY - drag.y) * k; draw(cv, 560);
        });
        stage.addEventListener('pointerup', function () { drag = null; });
        stage.addEventListener('wheel', function (e) { e.preventDefault(); zoom.value = String(Math.min(4, Math.max(1, z * (e.deltaY < 0 ? 1.1 : 0.9)))); zoom.dispatchEvent(new Event('input')); }, { passive: false });
        function done(v) { ov.remove(); URL.revokeObjectURL(url); document.removeEventListener('keydown', key, true); resolve(v); }
        function key(e) { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(null); } }
        document.addEventListener('keydown', key, true);
        no.addEventListener('click', function () { done(null); });
        ok.addEventListener('click', function () {
          var out = document.createElement('canvas'); out.width = out.height = 512; draw(out, 512);
          out.toBlob(function (b) { done(b); }, 'image/png');
        });
        ok.focus();
      };
      img.src = url;
    });
  }

  function avatarField(box, a) {
    var wrap = el('div', 'hk-avsec'); box.appendChild(wrap);
    wrap.appendChild(el('h4', null, T('Profilbild')));
    var row = el('div', 'hk-avset'), side = el('div', 'hk-avset-side');
    var pic = el('span', 'hk-avset-pic', String(a.name || a.username || '?').trim().charAt(0).toUpperCase());
    pic.setAttribute('aria-hidden', 'true'); pic.style.setProperty('--cm-h', String(hueOf(a.username)));
    row.appendChild(pic); row.appendChild(side); wrap.appendChild(row);
    var info = el('p', 'hk-hint'), btns = el('div', 'hk-avset-btns'), st = el('div', 'hk-status'); st.setAttribute('role', 'status');
    var up = el('button', 'btn', T('Bild hochladen …')), rm = el('button', 'btn', T('Eigenes Bild entfernen'));
    up.type = rm.type = 'button';
    var file = el('input'); file.type = 'file'; file.accept = 'image/png,image/jpeg,image/gif,image/webp,image/heic,image/heif,.heic,.heif'; file.hidden = true;
    btns.appendChild(up); btns.appendChild(rm); side.appendChild(info); side.appendChild(btns); side.appendChild(file); side.appendChild(st);
    function sw(label, on) {
      var l = el('label', 'hk-avset-row'), i = el('input', 'hk-switch'); i.type = 'checkbox'; i.setAttribute('role', 'switch'); i.checked = !!on;
      l.appendChild(el('span', null, label)); l.appendChild(i); wrap.appendChild(l); return i;
    }
    var gsw = sw(T('Gravatar verwenden, wenn ich kein eigenes Bild habe'), true), psw = sw(T('Auch auf meinen veröffentlichten Seiten zeigen'), false);
    wrap.appendChild(el('p', 'hk-hint', T('Gravatar-Bilder holt der Server ab und speichert sie zwischen – dein Browser und die Apps fragen nie direkt bei Gravatar.')));
    var SRC = { upload: T('Eigenes Bild'), sso: T('Bild vom Anmeldedienst'), gravatar: 'Gravatar' };
    function draw(s) {
      var old = pic.querySelector('.hk-av-img'); if (old) old.remove(); pic.classList.remove('hk-av-on');
      HK.avatarImg(pic, s.url, 72);
      headAvatar(s.url);
      info.textContent = s.source ? T('Angezeigt wird: {src}', { src: SRC[s.source] || s.source }) : T('Kein Bild – es werden deine Initialen angezeigt.');
      rm.hidden = !s.upload;
      gsw.checked = s.gravatar; gsw.disabled = !s.gravatar_enabled;
      gsw.parentNode.hidden = !s.gravatar_enabled;
      psw.checked = s.public;
      up.title = T('PNG, JPEG, GIF, WebP oder HEIC, höchstens {n} MB', { n: s.max_mb });
    }
    function fail(err) { status(st, err.message || String(err), 'err'); }
    api('GET', '/api/account/avatar').then(draw).catch(function () { wrap.remove(); });
    up.addEventListener('click', function () { file.value = ''; file.click(); });
    file.addEventListener('change', function () {
      var f = file.files && file.files[0]; if (!f) return;
      cropDialog(f).then(function (blob) {
        if (!blob) return;
        status(st, T('Wird hochgeladen …'));
        return fetch('/api/account/avatar', { method: 'PUT', credentials: 'same-origin', headers: { 'X-Saga': '1', 'Content-Type': /^image\//.test(blob.type) ? blob.type : 'image/heic' }, body: blob })
          .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status)); return d; }); })
          .then(function (d) { draw(d); status(st, T('✓ Gespeichert'), 'ok'); });
      }).catch(fail);
    });
    rm.addEventListener('click', function () {
      api('DELETE', '/api/account/avatar').then(function (d) { draw(d); status(st, T('✓ Entfernt'), 'ok'); }).catch(fail);
    });
    [[gsw, 'gravatar'], [psw, 'public']].forEach(function (p) {
      p[0].addEventListener('change', function () {
        var b = {}; b[p[1]] = p[0].checked; p[0].disabled = true;
        api('PATCH', '/api/account/avatar', b).then(function (d) { draw(d); status(st, T('✓ Gespeichert'), 'ok'); })
          .catch(function (err) { p[0].checked = !p[0].checked; fail(err); }).then(function () { p[0].disabled = false; });
      });
    });
  }

  // --- Abschnitt "Konto": Name, E-Mail, Passwort (nur lokale Konten), Gruppen ------------------------------
  function accountSection(body) {
    var sec = el('section', 'hk-set hk-set-account'); body.appendChild(sec);
    sec.appendChild(el('h3', null, T('Konto')));
    var box = el('div'); sec.appendChild(box);
    api('GET', '/api/account').then(function (a) {
      box.appendChild(el('p', 'hk-hint', a.username + ' · ' + a.source_label + (a.groups.length ? ' · ' + T('Gruppen: {list}', { list: a.groups.join(', ') }) : '')));
      avatarField(box, a);
      langField(box, a);
      if (window.HKLive) HKLive.settingsSection(box);          // Benachrichtigungen je Art (static/live.js)
      if (!a.can_edit) {
        box.appendChild(el('p', 'hk-hint', T('Name, E-Mail und Passwort verwaltet dein Anmeldedienst.')));
        if (window.HKSecurity) HKSecurity.section(box);          // Passkeys/2FA (LDAP-Konten; bei SSO nur ein Hinweis)
        publicNameField(box);
        return;
      }
      var nm = input('text', a.name === a.username ? '' : a.name, T('Anzeigename')); nm.maxLength = 120;
      var em = input('email', a.email, 'name@example.org'); em.maxLength = 200;
      box.appendChild(field(T('Anzeigename'), nm)); box.appendChild(field(T('E-Mail'), em));
      var st = el('div', 'hk-status'), bar = el('div', 'hk-actions'), sv = el('button', 'btn btn-primary', T('Speichern')); sv.type = 'button';
      bar.appendChild(sv); box.appendChild(bar); box.appendChild(st);
      sv.addEventListener('click', function () {
        api('PATCH', '/api/account', { name: nm.value.trim(), email: em.value.trim() }).then(function (d) {
          status(st, T('✓ Gespeichert'), 'ok'); btn.dataset.name = d.name;
          var nmEl = btn.querySelector('.hk-user-n'); if (nmEl) nmEl.textContent = d.name; else btn.textContent = '👤 ' + d.name + ' ▾';
          btn.setAttribute('aria-label', T('Benutzermenü') + ': ' + d.name);
        }).catch(function (err) { status(st, err.message, 'err'); });
      });
      box.appendChild(el('h4', null, T('Passwort ändern')));
      var o = input('password', '', T('Bisheriges Passwort')), n1 = input('password', '', T('mind. 10 Zeichen')), n2 = input('password', '', T('wiederholen'));
      o.autocomplete = 'current-password'; n1.autocomplete = n2.autocomplete = 'new-password';
      box.appendChild(field(T('Bisheriges'), o)); box.appendChild(field(T('Neues Passwort'), n1)); box.appendChild(field(T('Wiederholen'), n2));
      var st2 = el('div', 'hk-status'), bar2 = el('div', 'hk-actions'), pwb = el('button', 'btn', T('Passwort ändern')); pwb.type = 'button';
      bar2.appendChild(pwb); box.appendChild(bar2); box.appendChild(st2);
      box.appendChild(el('p', 'hk-hint', T('Andere Browser-Sitzungen und angemeldete Apps werden danach abgemeldet – außer dieser Sitzung und der App, in der du gerade bist.')));
      if (window.HKSecurity) HKSecurity.section(box);          // Anmeldung & Sicherheit: Passkeys, Authenticator-App (static/security.js)
      publicNameField(box);
      pwb.addEventListener('click', function () {
        if (n1.value !== n2.value) { status(st2, T('Die neuen Passwörter stimmen nicht überein.'), 'err'); return; }
        pwb.disabled = true;
        api('POST', '/api/account/password', { old: o.value, new: n1.value }).then(function () {
          o.value = n1.value = n2.value = ''; status(st2, T('✓ Passwort geändert'), 'ok');
        }).catch(function (err) { status(st2, err.message, 'err'); }).then(function () { pwb.disabled = false; });
      });
    }).catch(function (err) { box.textContent = T('Nicht geladen: {err}', { err: err.message }); });
  }

  // --- Abschnitt "Lesezeichen": eigene Kacheln auf dem Dashboard (auch in den Apps) -----------------------------
  function bmIcon(b) {
    var ic = el('span', 'ic' + (b.icon ? ' has-img' : ''));
    if (b.icon) {
      var img = el('img'); img.alt = ''; img.loading = 'lazy'; img.src = '/api/services/icon/' + encodeURIComponent(b.icon);
      img.addEventListener('error', function () { ic.classList.remove('has-img'); ic.textContent = (b.name || '?').charAt(0).toUpperCase(); });
      ic.appendChild(img);
    } else ic.textContent = (b.name || '?').charAt(0).toUpperCase();
    return ic;
  }
  function bookmarkSection(body) {
    var sec = el('section', 'hk-set hk-set-bookmarks'); body.appendChild(sec);
    sec.appendChild(el('h3', null, T('Lesezeichen')));
    sec.appendChild(el('p', 'hk-hint', T('Eigene Kacheln auf dem Dashboard – nur für dich sichtbar, auch in den Apps. Symbole: Name von selfh.st/icons (z. B. „jellyfin“).')));
    var list = el('div'); sec.appendChild(list);
    var add = el('button', 'btn', T('+ Lesezeichen')); add.type = 'button'; sec.appendChild(add);
    var formBox = el('div'); sec.appendChild(formBox);
    function load() {
      api('GET', '/api/bookmarks').then(function (d) {
        list.textContent = '';
        if (!d.length) list.appendChild(el('p', 'hk-hint', T('Noch keine Lesezeichen.')));
        d.forEach(function (b) {
          var row = el('div', 'hk-bm-row'), txt = el('div');
          txt.appendChild(el('strong', null, b.name)); txt.appendChild(el('div', 'hk-hint', b.category + ' · ' + b.url));
          var ed = el('button', 'btn', T('Bearbeiten')), del = el('button', 'btn btn-danger', T('Löschen')); ed.type = del.type = 'button';
          ed.addEventListener('click', function () { bmForm(formBox, b, function () { load(); changed = true; }); });
          del.addEventListener('click', function () {
            if (!confirm(T('Lesezeichen „{name}“ löschen?', { name: b.name }))) return;
            api('DELETE', '/api/bookmarks/' + b.id).then(function () { load(); changed = true; }).catch(function (err) { HK.toast(err.message); });
          });
          row.appendChild(bmIcon(b)); row.appendChild(txt); row.appendChild(ed); row.appendChild(del); list.appendChild(row);
        });
      }).catch(function (err) { list.textContent = T('Nicht geladen: {err}', { err: err.message }); });
    }
    add.addEventListener('click', function () { bmForm(formBox, null, function () { load(); changed = true; }); });
    load();
  }
  var changed = false;              // Lesezeichen geaendert -> Dashboard beim Schliessen neu laden
  function bmForm(box, b, done) {
    box.textContent = '';
    var f = el('div', 'hk-cal-form');
    var nm = input('text', b ? b.name : '', T('z. B. Router')), url = input('url', b ? b.url : '', 'https://…');
    var ic = input('text', b ? b.icon : '', T('optional, z. B. openwrt')), cat = input('text', b ? b.category : T('Lesezeichen'));
    nm.maxLength = 80; url.maxLength = 500; ic.maxLength = 64; cat.maxLength = 40;
    // Adresse zuerst (Pflichtfeld, Cursor steht dort): ohne Namen nimmt der Server den Rechnernamen der Adresse -
    // den zeigt das Namensfeld schon beim Tippen als Vorschlag.
    [field(T('Adresse'), url), field(T('Name'), nm), field(T('Symbol'), ic), field(T('Kategorie'), cat)].forEach(function (x) { f.appendChild(x); });
    var nmHint = nm.placeholder;
    url.addEventListener('input', function () {
      var v = url.value.trim(), h = '';
      try { if (v) h = new URL(/:\/\//.test(v) ? v : 'https://' + v).host; } catch (e) { h = ''; }
      nm.placeholder = h || nmHint;
    });
    if (b) url.dispatchEvent(new Event('input'));
    var bar = el('div', 'hk-actions'), sv = el('button', 'btn btn-primary', b ? T('Speichern') : T('Hinzufügen')), cc = el('button', 'btn', T('Abbrechen')), st = el('div', 'hk-status');
    sv.type = cc.type = 'button'; bar.appendChild(sv); bar.appendChild(cc); f.appendChild(bar); f.appendChild(st);
    box.appendChild(f);
    // Abschnitte darueber laden nach -> erst danach zum Formular scrollen
    setTimeout(function () { f.scrollIntoView({ block: 'center' }); (b ? nm : url).focus({ preventScroll: true }); }, 350);   // neu: erstes Feld (Adresse), bearbeiten: Name
    cc.addEventListener('click', function () { box.textContent = ''; });
    function save() {
      var data = { name: nm.value.trim(), url: url.value.trim(), icon: ic.value.trim(), category: cat.value.trim() };
      (b ? api('PATCH', '/api/bookmarks/' + b.id, data) : api('POST', '/api/bookmarks', data))
        .then(function () { box.textContent = ''; HK.toast(b ? T('Lesezeichen gespeichert') : T('Lesezeichen hinzugefügt')); done(); })
        .catch(function (err) { status(st, err.message, 'err'); });
    }
    sv.addEventListener('click', save);
    f.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); save(); } });
  }
  // Einzelnes Lesezeichen direkt vom Dashboard aus bearbeiten/anlegen (✎ an der Kachel, "+ Lesezeichen")
  function editBookmark(id) {
    openSettings('bookmarks');
    var box = dlg && dlg.querySelector('.hk-set-bookmarks > div:last-child');
    if (!box) return;
    if (!id) { bmForm(box, null, function () { changed = true; closeSettings(); }); return; }
    api('GET', '/api/bookmarks').then(function (d) {
      var b = d.find(function (x) { return String(x.id) === String(id); });
      if (b) bmForm(box, b, function () { changed = true; closeSettings(); });
    });
  }
  var _close = closeSettings;
  closeSettings = function () {
    _close();
    if (changed && document.getElementById('bm-add')) { changed = false; location.reload(); }
  };
  window.HKSettings = { open: openSettings, editBookmark: editBookmark };
})();
